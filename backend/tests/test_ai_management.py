"""Real SDK integration with offline drivers: no paid calls or ambient credentials."""
import json
from types import SimpleNamespace

import pytest

from app import db
from app.models import User
from app.models.ai import AiConversation, AiRun
from app.services import ai_connections, ai_management, ai_service, ai_usage
from factories import make_user, headers_for


@pytest.fixture
def setup_ai(app, auth_headers, monkeypatch):
    from prompture.drivers.base import Driver
    calls = []

    class FakeDriver(Driver):
        supports_messages = True
        supports_streaming = True

        def __init__(self, connection, model):
            self.connection_id, self.model = connection.id, model
            self.key = connection.config['api_key']

        def generate(self, prompt, options):
            calls.append((self.connection_id, self.model, self.key, options))
            if self.model == 'fail':
                raise RuntimeError('provider-secret-must-not-leak')
            text = json.dumps({'summary': 'Healthy', 'facts': ['CPU normal'], 'warnings': []}) if self.model == 'json' else 'Healthy'
            return {'text': text, 'meta': {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10, 'cost': 0.01}}

        def generate_messages(self, messages, options):
            return self.generate(messages, options)

        def generate_messages_stream(self, messages, options):
            result = self.generate(messages, options)
            yield {'type': 'delta', 'text': result['text']}
            yield {'type': 'done', **result}

    monkeypatch.setattr(ai_service, 'ensure_initialized', lambda: None)
    monkeypatch.setattr(ai_service, 'is_enabled', lambda: True)
    monkeypatch.setattr(ai_service, 'is_configured', lambda: True)
    monkeypatch.setattr(ai_service, 'injection_flagged', lambda text: False)
    monkeypatch.setattr(ai_service, 'redact_input', lambda text: text)
    monkeypatch.setattr(ai_service, '_maybe_redact_result', lambda data: data)
    monkeypatch.setattr(ai_service, 'build_tool_registry', lambda *a, **kw: None)
    monkeypatch.setattr(ai_service, 'build_system_prompt', lambda *a, **kw: 'Use provided data')
    monkeypatch.setattr(ai_connections, 'build_driver', lambda connection, model=None: FakeDriver(connection, model or connection.model))
    monkeypatch.setattr(ai_management, 'model_info', lambda connection, model: {
        'pricing': {'input': 1, 'output': 1}, 'max_output_tokens': 8192,
        'temperature': True, 'reasoning_effort': True, 'tools': True,
    })
    with app.app_context():
        def connection(name, key):
            return ai_connections.save({'name': name, 'provider': 'openai_compatible', 'model': 'main',
                                       'config': {'endpoint': 'http://offline.test/v1', 'api_key': key}}).id
        first, second = connection('Primary', 'first-secret'), connection('Fallback', 'second-secret')
    return SimpleNamespace(first=first, second=second, calls=calls, driver=FakeDriver)


def configure(client, headers, **changes):
    response = client.put('/api/v1/ai/management', headers=headers, json=changes)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def turn(client, headers, stream=False, **body):
    response = client.post('/api/v1/ai/chat' + ('/stream' if stream else ''), headers=headers,
                           json={'message': 'Check the server', 'mode': 'simple', **body})
    if not stream:
        return response, response.get_json()
    events = []
    for block in response.get_data(as_text=True).split('\n\n'):
        if block.startswith('event:'):
            lines = block.splitlines()
            events.append((lines[0][7:], json.loads(lines[1][6:])))
    errors = [data for name, data in events if name == 'error']
    done = next(data for name, data in events if name == 'done')
    return response, {**done, 'errors': errors, 'events': events}


@pytest.mark.parametrize('stream', [False, True])
def test_profile_runs_resume_and_account_only_new_usage(app, client, auth_headers, setup_ai, stream):
    config = ai_management.DEFAULTS.copy()
    config['profiles'] = {'utility': {'connection_id': setup_ai.second, 'model': 'team/cheap'}, 'standard': None, 'advanced': None}
    configure(client, auth_headers, **config)
    response, first = turn(client, auth_headers, stream, workflow='summarize')
    assert response.status_code == 200, first
    assert first.get('errors', []) == []
    assert first['usage']['profile'] == 'utility'
    assert first['usage']['total_tokens'] == 10
    assert setup_ai.calls[-1][0:3] == (setup_ai.second, 'team/cheap', 'second-secret')
    configure(client, auth_headers, profiles={'utility': None, 'standard': None, 'advanced': None})
    _, second = turn(client, auth_headers, stream, conversation_id=first['conversation_id'])
    assert second['usage']['total_tokens'] == 10
    assert second['usage']['cost'] == pytest.approx(0.01)
    assert setup_ai.calls[-1][0] == setup_ai.second
    report = client.get('/api/v1/ai/usage', headers=auth_headers).get_json()
    assert report['totals']['tokens'] == 20
    assert report['totals']['cost'] == pytest.approx(0.02)
    with app.app_context():
        row = db.session.get(AiConversation, first['conversation_id'])
        assert row.export['usage']['total_tokens'] == 20
        assert row.messages.filter_by(role='assistant').count() == 2
        assert all(message.usage['total_tokens'] == 10 for message in row.messages.filter_by(role='assistant'))


@pytest.mark.parametrize('stream', [False, True])
def test_fallback_uses_own_credentials_and_records_failure(client, auth_headers, setup_ai, stream):
    configure(client, auth_headers, fallback_enabled=True,
              fallbacks=[{'connection_id': setup_ai.second, 'model': 'recovery'}])
    response, body = turn(client, auth_headers, stream, connection_id=setup_ai.first, model='fail')
    assert response.status_code == 200, body
    assert body.get('errors', []) == []
    assert [call[2] for call in setup_ai.calls] == ['first-secret', 'second-secret']
    assert body['usage']['connection_id'] == setup_ai.second
    assert [a['status'] for a in body['usage']['attempts']] == ['error', 'success']
    assert 'secret' not in json.dumps(body)


def test_no_fallback_after_streamed_output(client, auth_headers, setup_ai, monkeypatch):
    def partial(self, messages, options):
        yield {'type': 'delta', 'text': 'Partial'}
        raise RuntimeError('private-error')
    monkeypatch.setattr(setup_ai.driver, 'generate_messages_stream', partial)
    configure(client, auth_headers, fallback_enabled=True,
              fallbacks=[{'connection_id': setup_ai.second, 'model': 'recovery'}])
    _, body = turn(client, auth_headers, True)
    assert body['errors']
    assert len(body['usage']['attempts']) == 1
    assert body['usage']['connection_id'] == setup_ai.first
    assert 'private-error' not in json.dumps(body)


def test_preview_and_execution_use_only_approved_candidates(client, auth_headers, setup_ai, monkeypatch):
    from prompture.pipeline.routing import ModelRouter
    monkeypatch.setattr(ModelRouter, '_get_available_models', lambda self: pytest.fail('Ambient model discovery'))
    configure(client, auth_headers, routing_enabled=True, strategy='quality_first', pool=[
        {'connection_id': setup_ai.first, 'model': 'small', 'tier': 'budget'},
        {'connection_id': setup_ai.second, 'model': 'deep', 'tier': 'premium'},
    ])
    preview = client.post('/api/v1/ai/management/preview', headers=auth_headers, json={'message': 'Check the server'}).get_json()
    _, result = turn(client, auth_headers)
    assert preview['connection_id'] == result['usage']['connection_id'] == setup_ai.second
    assert setup_ai.calls[-1][1] == preview['model'] == 'deep'
    assert client.delete('/api/v1/ai/connections/' + setup_ai.second, headers=auth_headers).status_code == 409


def test_generation_options_are_applied_and_extraction_is_structured(client, auth_headers, setup_ai):
    configure(client, auth_headers, max_output_tokens=1024, temperature=0.3, reasoning_effort='high',
              profiles={'utility': {'connection_id': setup_ai.first, 'model': 'json'}, 'standard': None, 'advanced': None})
    response, body = turn(client, auth_headers, workflow='extract')
    assert response.status_code == 200, body
    assert json.loads(body['reply']) == {'summary': 'Healthy', 'facts': ['CPU normal'], 'warnings': []}
    assert setup_ai.calls[-1][-1]['max_tokens'] == 1024
    assert setup_ai.calls[-1][-1]['temperature'] == 0.3
    assert setup_ai.calls[-1][-1]['reasoning_effort'] == 'high'


def test_run_finish_is_idempotent_and_quota_reservations_block_concurrent_runs(app, client, auth_headers, setup_ai):
    configure(client, auth_headers, monthly_limit_usd=0.5, reservation_usd=0.5)
    with app.app_context():
        from app.exceptions import ConflictError
        user = User.query.filter_by(username='testadmin').one()
        row = AiConversation(user_id=user.id, connection_id=setup_ai.first, model_name='openai_compatible/main', management_json='{}')
        db.session.add(row); db.session.commit()
        recorder = ai_usage.start(row, user)
        with pytest.raises(ConflictError):
            ai_usage.start(row, user)
        recorder.record({'cost': 0.1, 'total_tokens': 10}, 10)
        recorder.finish('success')
        recorder.finish('success')
        assert AiRun.query.count() == 1
        assert AiRun.query.one().cost == pytest.approx(0.1)
        assert ai_usage.quota_status(ai_management.settings(), user.id, None)[0]['remaining'] == pytest.approx(0.4)


def test_management_and_reports_are_admin_only(app, client, setup_ai):
    with app.app_context():
        headers = headers_for(make_user(db, role='viewer'))
    assert client.get('/api/v1/ai/management', headers=headers).status_code == 403
    assert client.get('/api/v1/ai/usage', headers=headers).status_code == 403
    assert client.put('/api/v1/ai/management', headers=headers, json={'fallback_enabled': True}).status_code == 403
    assert client.post('/api/v1/ai/management/preview', headers=headers, json={'message': 'x'}).status_code == 403


@pytest.mark.parametrize('payload', [
    {'max_tokens': -1}, {'max_output_tokens': 'NaN'}, {'routing_enabled': 'yes'},
    {'routing_enabled': True, 'pool': []}, {'fallback_enabled': True, 'fallbacks': []},
    {'profiles': {'utility': None}}, {'reservation_usd': 0}, {'reasoning_effort': 'unknown'},
])
def test_invalid_configuration_is_rejected(client, auth_headers, setup_ai, payload):
    assert client.put('/api/v1/ai/management', headers=auth_headers, json=payload).status_code == 400


def test_explicit_role_and_override_win_over_automatic_routing(client, auth_headers, setup_ai):
    configure(client, auth_headers, routing_enabled=True, pool=[
        {'connection_id': setup_ai.second, 'model': 'routed', 'tier': 'premium'}],
        profiles={'utility': {'connection_id': setup_ai.first, 'model': 'cheap'},
                  'standard': None, 'advanced': None})
    _, body = turn(client, auth_headers, profile='utility')
    assert body['usage']['model'] == 'openai_compatible/cheap'
    _, body = turn(client, auth_headers, profile='utility', connection_id=setup_ai.second, model='manual/path')
    assert body['usage']['model'] == 'openai_compatible/manual/path'


@pytest.mark.parametrize('stream', [False, True])
def test_token_budget_is_retained_on_resume(client, auth_headers, setup_ai, stream):
    configure(client, auth_headers, max_tokens=10)
    _, first = turn(client, auth_headers, stream)
    response, second = turn(client, auth_headers, stream, conversation_id=first['conversation_id'])
    assert len(setup_ai.calls) == 1
    assert response.status_code == (200 if stream else 409)
    assert second['usage']['total_tokens'] == 0
    assert second['usage']['budget']['tokens_remaining'] == 0
    report = client.get('/api/v1/ai/usage', headers=auth_headers).get_json()
    assert report['totals']['tokens'] == 10
    assert report['totals']['errors'] == 1


@pytest.mark.parametrize('known', [False, True])
def test_degradation_switches_only_to_known_cheaper_connection(app, client, auth_headers, setup_ai, monkeypatch, known):
    from app.models.system_settings import SystemSettings
    _, first = turn(client, auth_headers)
    with app.app_context():
        SystemSettings.set('ai_max_cost_usd', '0.012')
        db.session.commit()
    configure(client, auth_headers, budget_policy='degrade',
              fallbacks=[{'connection_id': setup_ai.second, 'model': 'cheaper'}])
    def info(connection, model):
        return {'pricing': ({'input': 1, 'output': 1} if connection.id == setup_ai.first else
                            {'input': 0.1, 'output': 0.1} if known else None),
                'max_output_tokens': 8192, 'temperature': False, 'reasoning_effort': False}
    monkeypatch.setattr(ai_management, 'model_info', info)
    _, second = turn(client, auth_headers, conversation_id=first['conversation_id'])
    assert second['usage']['connection_id'] == (setup_ai.second if known else setup_ai.first)
    assert second['usage']['budget']['exceeded'] is True


def test_scoped_allowances_and_unknown_cost_are_conservative(app, client, auth_headers, setup_ai):
    from factories import make_workspace
    configure(client, auth_headers, user_monthly_limit_usd=1, workspace_monthly_limit_usd=1)
    with app.app_context():
        user = User.query.filter_by(username='testadmin').one()
        other = make_user(db)
        workspace = make_workspace(db, created_by=user.id)
        row = AiConversation(user_id=user.id, connection_id=setup_ai.first, model_name='openai_compatible/main')
        row.management = {'workspace_id': workspace.id}
        db.session.add(row); db.session.commit()
        recorder = ai_usage.start(row, user)
        recorder.record({'total_tokens': 10, 'cost': 0, 'cost_source': 'unknown'}, 10)
        recorder.finish('success')
        mine = ai_usage.quota_status(ai_management.settings(), user.id, workspace.id)
        theirs = ai_usage.quota_status(ai_management.settings(), other.id, None)
        assert all(item['used'] == 0.5 for item in mine)
        assert theirs[0]['used'] == 0
        assert AiRun.query.one().cost == 0
        assert AiRun.query.one().budget_charge == 0.5
        assert ai_usage.report({'user_id': other.id})['totals']['runs'] == 0
        assert ai_usage.report({'workspace_id': workspace.id})['totals']['tokens'] == 10


def test_resume_rechecks_workspace_membership(app, client, auth_headers, setup_ai):
    from factories import make_workspace
    from app.exceptions import PermissionDeniedError
    with app.app_context():
        owner, outsider = make_user(db), make_user(db)
        workspace = make_workspace(db, created_by=owner.id)
        row = AiConversation(user_id=outsider.id, connection_id=setup_ai.first, model_name='openai_compatible/main')
        row.management = {'workspace_id': workspace.id}
        db.session.add(row); db.session.commit()
        with pytest.raises(PermissionDeniedError):
            ai_usage.start(row, outsider)
        assert AiRun.query.count() == 0


@pytest.mark.parametrize('payload', [{'workflow': {}}, {'profile': []}, {'workflow': 10}])
def test_invalid_task_payload_is_a_client_error(client, auth_headers, setup_ai, payload):
    assert turn(client, auth_headers, **payload)[0].status_code == 400
    assert client.post('/api/v1/ai/management/preview', headers=auth_headers,
                       json={'message': 'test', **payload}).status_code == 400


def test_usage_survives_transcript_deletion(app, client, auth_headers, setup_ai):
    _, first = turn(client, auth_headers)
    assert client.delete('/api/v1/ai/conversations/' + first['conversation_id'], headers=auth_headers).status_code == 200
    with app.app_context():
        assert AiRun.query.one().conversation_id is None
        assert AiRun.query.one().total_tokens == 10


def test_read_only_registry_never_exposes_write_tools(app, monkeypatch):
    from app.services.ai_tool_registry import ToolDescriptor, ai_tool_registry
    with app.app_context():
        user = make_user(db, role='admin')
        descriptors = [ToolDescriptor(name, 'test__' + name, lambda: 'ok', name, {},
                                      plugin_slug='test', is_write=write)
                       for name, write in [('read', False), ('write', True)]]
        monkeypatch.setattr(ai_tool_registry, 'list_for', lambda *a: descriptors)
        registry = ai_service.build_tool_registry(user, 'assistant', None, read_only=True)
        assert registry.get('test__read') is not None
        assert registry.get('test__write') is None


def test_management_migration_preserves_existing_data_and_ledger_foreign_keys():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).parents[1] / 'migrations/versions/099_ai_management.py'
    spec = importlib.util.spec_from_file_location('migration099', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.exec_driver_sql('PRAGMA foreign_keys=ON')
        conn.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('CREATE TABLE workspaces (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('CREATE TABLE ai_provider_connections (id TEXT PRIMARY KEY, config_json TEXT)')
        conn.exec_driver_sql('CREATE TABLE ai_conversations (id TEXT PRIMARY KEY, export_json TEXT)')
        conn.exec_driver_sql("INSERT INTO ai_conversations VALUES ('chat', '{\"usage\":{\"cost\":0.5}}')")
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade(); migration.upgrade()
        assert json.loads(conn.exec_driver_sql('SELECT export_json FROM ai_conversations').scalar())['usage']['cost'] == 0.5
        conn.exec_driver_sql("INSERT INTO ai_runs (id, conversation_id, profile, workflow, strategy, status, created_at) VALUES ('run', 'chat', 'standard', 'chat', 'explicit', 'success', CURRENT_TIMESTAMP)")
        conn.exec_driver_sql("DELETE FROM ai_conversations WHERE id='chat'")
        assert conn.exec_driver_sql('SELECT conversation_id FROM ai_runs').scalar() is None
        assert len(sa.inspect(conn).get_indexes('ai_runs')) == 6
        migration.downgrade()
        assert 'ai_runs' not in sa.inspect(conn).get_table_names()


@pytest.mark.parametrize('stream', [False, True])
def test_plugin_calls_use_task_models_and_shared_usage(app, client, auth_headers, setup_ai, monkeypatch, stream):
    from app.plugins_sdk import ai as sdk_ai
    from app import plugins_sdk
    from app.exceptions import PermissionDeniedError
    configure(client, auth_headers, profiles={
        'utility': {'connection_id': setup_ai.second, 'model': 'plugin/cheap'}, 'standard': None, 'advanced': None})
    with app.app_context():
        user = User.query.filter_by(username='testadmin').one()
        monkeypatch.setattr(plugins_sdk, 'current_user', lambda: user)
        result = ''.join(sdk_ai.ask_stream('Summarize', workflow='summarize')) if stream else sdk_ai.ask('Summarize', workflow='summarize')
        assert result == 'Healthy'
        run = AiRun.query.one()
        assert run.conversation_id is None
        assert run.model == 'openai_compatible/plugin/cheap'
        assert run.status == 'success' and run.total_tokens == 10
        monkeypatch.setattr(ai_service, 'is_enabled', lambda: False)
        with pytest.raises(PermissionDeniedError):
            sdk_ai.ask('Disabled')
        assert AiRun.query.count() == 1


def test_restart_reconciles_abandoned_reservation_once(app, client, auth_headers, setup_ai):
    with app.app_context():
        app.extensions.pop('ai_usage_recovered', None)  # simulate a new worker
        run = AiRun(reserved_cost=0.5, cost=0.1)
        db.session.add(run); db.session.commit()
        ai_usage.recover_interrupted()
        assert run.status == 'interrupted'
        assert run.budget_charge == 0.5 and run.reserved_cost == 0
        assert run.usage['cost_source'] == 'unknown'
        active = AiRun(reserved_cost=0.5)
        db.session.add(active); db.session.commit()
        ai_usage.recover_interrupted()
        assert active.status == 'running'


def test_fallback_does_not_replay_completed_tools(app, client, auth_headers, setup_ai, monkeypatch):
    from prompture.agents.tools_schema import ToolDefinition, ToolRegistry
    invoked, attempts = [], []
    registry = ToolRegistry()
    registry.add(ToolDefinition(name='peek', description='Read status',
                               parameters={'type': 'object', 'properties': {}},
                               function=lambda: invoked.append('peek') or 'healthy'))
    monkeypatch.setattr(ai_service, 'build_tool_registry', lambda *a, **kw: registry)
    monkeypatch.setattr(setup_ai.driver, 'supports_tool_use', True)
    def native(self, messages, tools, options):
        attempts.append(self.connection_id)
        if len(attempts) > 1:
            raise RuntimeError('Provider failed after a tool response')
        return {'text': '', 'tool_calls': [{'id': 'call1', 'name': 'peek', 'arguments': {}}],
                'meta': {'total_tokens': 10, 'cost': 0.01}}
    monkeypatch.setattr(setup_ai.driver, 'generate_messages_with_tools', native)
    configure(client, auth_headers, fallback_enabled=True,
              fallbacks=[{'connection_id': setup_ai.second, 'model': 'recovery'}])
    response, body = turn(client, auth_headers, mode='assistant')
    assert response.status_code == 500
    assert invoked == ['peek']
    assert attempts == [setup_ai.first, setup_ai.first]
    with app.app_context():
        run = AiRun.query.one()
        assert run.cost == pytest.approx(0.01) and run.total_tokens == 10
        assert run.status == 'error'
        assert db.session.get(AiConversation, run.conversation_id).export['usage']['cost'] == pytest.approx(0.01)


def test_warn_policy_continues_and_reports_exceeded_allowance(client, auth_headers, setup_ai):
    configure(client, auth_headers, max_tokens=10, budget_policy='warn_and_continue')
    _, first = turn(client, auth_headers)
    response, second = turn(client, auth_headers, conversation_id=first['conversation_id'])
    assert response.status_code == 200
    assert len(setup_ai.calls) == 2
    assert second['usage']['budget']['exceeded'] is True
    assert second['usage']['budget']['policy'] == 'warn_and_continue'


@pytest.mark.parametrize('payload', [{'enabled': 'false'}, {'fallback_models': ['ambient/model']}])
def test_settings_reject_ambiguous_enable_and_unbound_fallbacks(client, auth_headers, setup_ai, payload):
    assert client.put('/api/v1/ai/settings', headers=auth_headers, json=payload).status_code == 400
