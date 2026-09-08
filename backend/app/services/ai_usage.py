"""Per-run accounting and scoped spending admission for the single panel worker."""
from datetime import datetime
import threading
import time

from sqlalchemy import func
from flask import current_app

from app import db
from app.exceptions import ConflictError
from app.models.ai import AiRun
from app.services import ai_management

_admission_lock = threading.RLock()


def recover_interrupted():
    """Reconcile runs left by the previous single worker, once per app boot."""
    with _admission_lock:
        if current_app.extensions.get('ai_usage_recovered'):
            return
        for run in AiRun.query.filter_by(status='running').all():
            run.status = 'interrupted'
            run.completed_at = datetime.utcnow()
            run.budget_charge = max(run.cost, run.reserved_cost)
            run.reserved_cost = 0
            run.usage = {**run.usage, 'cost_source': 'unknown'}
        db.session.commit()
        current_app.extensions['ai_usage_recovered'] = True


def _month():
    return datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def quota_status(config, user_id, workspace_id):
    results = []
    for scope, limit, predicate in (
        ('Panel', config['monthly_limit_usd'], None),
        ('User', config['user_monthly_limit_usd'], AiRun.user_id == user_id),
        ('Workspace', config['workspace_monthly_limit_usd'], AiRun.workspace_id == workspace_id),
    ):
        if not limit or (scope == 'Workspace' and workspace_id is None) or (scope == 'User' and user_id is None):
            continue
        query = db.session.query(func.coalesce(func.sum(AiRun.budget_charge + AiRun.reserved_cost), 0)).filter(AiRun.created_at >= _month())
        if predicate is not None:
            query = query.filter(predicate)
        used = float(query.scalar())
        results.append({'scope': scope, 'limit': limit, 'used': used, 'remaining': max(0, limit - used)})
    return results


def start(row, user):
    recover_interrupted()
    config = ai_management.settings()
    selection = row.management
    workspace = ai_management.workspace_id(user, selection.get('workspace_id'))
    with _admission_lock:
        quotas = quota_status(config, user.id, workspace)
        reservation = config['reservation_usd'] if quotas else 0
        if any(item['remaining'] < reservation for item in quotas):
            raise ConflictError('Monthly AI allowance has insufficient room for another run.', code='ai_quota_exceeded')
        run = AiRun(conversation_id=row.id, user_id=user.id, workspace_id=workspace,
                    connection_id=row.connection_id, model=row.model_name,
                    profile=selection.get('profile', 'standard'), workflow=selection.get('workflow', 'chat'),
                    strategy=selection.get('strategy', 'explicit'), reason=selection.get('reason', 'Saved conversation'),
                    reserved_cost=reservation)
        db.session.add(run)
        db.session.commit()
    return Recorder(run, config, row.export.get('usage', {}) if row.export else {})


class Recorder:
    def __init__(self, run, config, initial):
        from prompture import UsageSession
        self.run = run
        self.config = config
        self.initial = initial
        self.session = UsageSession()
        self.attempts = []
        self.started = time.monotonic()
        self.connection_id = run.connection_id
        self.model = run.model
        self.switched = False
        self.output_started = False
        self.call_started = 0

    def check(self, max_cost):
        from prompture.infra.budget import BudgetState, enforce_budget
        # Conversation's own guard remains active; this also covers SDK JSON
        # repair calls and spending from partial provider responses.
        state = BudgetState(cost_used=self.initial.get('cost', 0) + self.session.cost,
                            tokens_used=int(self.initial.get('total_tokens', 0)) + self.session.total_tokens,
                            max_cost=max_cost, max_tokens=self.config['max_tokens'] or None)
        policy = self.config['budget_policy']
        enforce_budget(state, 'hard_stop' if policy == 'degrade' else policy)
        if self.run.reserved_cost:
            enforce_budget(BudgetState(cost_used=self.session.cost, max_cost=self.run.reserved_cost), 'hard_stop')

    def record(self, meta, elapsed_ms, *, error=False):
        info = {'driver': (self.connection_id or 'legacy') + ':' + (self.model or 'unknown'), 'meta': meta, 'elapsed_ms': elapsed_ms}
        if error:
            self.session.record_error({})
        if meta:
            self.session.record(info)
        source = meta.get('cost_source', 'unknown')
        if source not in ('provider_reported', 'estimated'):
            source = 'estimated' if meta.get('cost', 0) > 0 else 'unknown'
        self.attempts.append({'connection_id': self.connection_id, 'model': self.model,
                              'status': 'error' if error else 'success', 'cost_source': source,
                              'cost': meta.get('cost', 0), 'total_tokens': meta.get('total_tokens', 0),
                              'duration_ms': elapsed_ms})
        self._persist()

    def summary(self):
        from prompture.infra.budget import BudgetState
        from app.services.ai_service import _max_cost
        result = self.session.summary()
        result.pop('formatted', None)
        sources = {a['cost_source'] for a in self.attempts}
        budget = BudgetState(cost_used=self.initial.get('cost', 0) + self.session.cost,
                             tokens_used=int(self.initial.get('total_tokens', 0)) + self.session.total_tokens,
                             max_cost=_max_cost(), max_tokens=self.config['max_tokens'] or None)
        return {**result, 'run_id': self.run.id, 'profile': self.run.profile,
                'workflow': self.run.workflow, 'model': self.model, 'connection_id': self.connection_id,
                'strategy': self.run.strategy, 'reason': self.run.reason, 'attempts': self.attempts,
                'cost_source': 'unknown' if not sources or 'unknown' in sources else ('provider_reported' if sources == {'provider_reported'} else 'estimated'),
                'duration_ms': (time.monotonic() - self.started) * 1000,
                'budget': {'cost_remaining': budget.cost_remaining, 'tokens_remaining': budget.tokens_remaining,
                           'exceeded': budget.exceeded, 'policy': self.config['budget_policy']}}

    def _persist(self):
        self.run.usage = self.summary()
        self.run.cost = self.session.cost
        self.run.total_tokens = self.session.total_tokens
        self.run.connection_id, self.run.model = self.connection_id, self.model
        db.session.commit()

    def finish(self, status, conv=None):
        with _admission_lock:
            if self.run.status != 'running':
                return self.run.usage
            # Compatibility with unwrapped legacy/test conversations. Difference
            # cumulative snapshots once, never sum historical turn snapshots.
            if not self.attempts and conv is not None and getattr(conv, 'usage', None):
                usage = conv.usage
                delta = {k: max(0, usage.get(k, 0) - self.initial.get(k, 0)) for k in
                         ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cost')}
                if any(delta.values()):
                    self.record(delta, (time.monotonic() - self.started) * 1000)
            self.run.status = status
            if conv is not None and hasattr(conv, '_usage'):
                # Prompture may not fold a partial/failed stream or JSON repair
                # into Conversation usage. Preserve the observed lifetime floor
                # for the next turn, without counting successful calls twice.
                for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cost'):
                    conv._usage[key] = max(conv._usage.get(key, 0), self.initial.get(key, 0) + getattr(self.session, key))
            self.run.completed_at = datetime.utcnow()
            self.run.duration_ms = (time.monotonic() - self.started) * 1000
            unknown = any(a['cost_source'] == 'unknown' for a in self.attempts)
            # Unknown billed usage cannot silently reopen a scoped allowance.
            self.run.budget_charge = max(self.session.cost, self.run.reserved_cost if unknown else 0)
            self.run.reserved_cost = 0
            self._persist()
            return self.run.usage


def report(args):
    """Admin-only caller. Aggregate the full filtered set, paginate only detail."""
    recover_interrupted()
    days = ai_management.number(args.get('days', 30), 'Days', 1, 366, integer=True)
    from datetime import timedelta
    query = AiRun.query.filter(AiRun.created_at >= datetime.utcnow() - timedelta(days=days))
    for key in ('profile', 'connection_id', 'user_id', 'workspace_id'):
        value = args.get(key)
        if value:
            if key.endswith('_id') and key != 'connection_id':
                value = ai_management.number(value, key, 1, 2147483647, integer=True)
            query = query.filter(getattr(AiRun, key) == value)
    rows = query.order_by(AiRun.created_at.desc()).all()
    groups = {key: {} for key in ('daily', 'profile', 'model', 'connection_id', 'user_id', 'workspace_id')}
    totals = {'runs': len(rows), 'cost': 0, 'tokens': 0, 'errors': 0, 'unknown_cost_runs': 0, 'duration_ms': 0}
    for row in rows:
        totals['cost'] += row.cost
        totals['tokens'] += row.total_tokens
        totals['errors'] += row.status in ('error', 'budget_exceeded', 'interrupted')
        totals['unknown_cost_runs'] += row.usage.get('cost_source') == 'unknown'
        totals['duration_ms'] += row.duration_ms
        for key, buckets in groups.items():
            label = row.created_at.date().isoformat() if key == 'daily' else str(getattr(row, key) or 'Unassigned')
            item = buckets.setdefault(label, {'label': label, 'cost': 0, 'tokens': 0, 'runs': 0, 'errors': 0})
            item['cost'] += row.cost
            item['tokens'] += row.total_tokens
            item['runs'] += 1
            item['errors'] += row.status in ('error', 'budget_exceeded', 'interrupted')
    totals['average_duration_ms'] = totals['duration_ms'] / len(rows) if rows else 0
    offset = ai_management.number(args.get('offset', 0), 'Offset', 0, 10000000, integer=True)
    return {'totals': totals, 'groups': {key: list(value.values()) for key, value in groups.items()},
            'allowances': quota_status(ai_management.settings(), args.get('user_id'), args.get('workspace_id')),
            'runs': [row.to_dict() for row in rows[offset:offset + 50]],
            'has_more': offset + 50 < len(rows), 'next_offset': offset + 50}
