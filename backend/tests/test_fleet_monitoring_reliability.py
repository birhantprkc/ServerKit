"""Fleet monitoring actually monitors: it runs, it keeps its data, it tells you.

Three defects, found together because they are the same failure wearing three
hats — monitoring that looks configured and is not:

1. ``FleetMonitorService.check_fleet_thresholds`` was correct and had no caller
   anywhere in the backend. Thresholds could be set on the Fleet page and were
   never evaluated, so a server could sit at 99% CPU for a week in silence.
2. ``agent_registry._store_metrics`` wrote five of the thirteen columns
   ``ServerMetrics`` has. Two of the eight it dropped were ``network_rx_rate``
   and ``network_tx_rate`` — exactly what ``METRIC_COLUMNS`` reads — so a
   network threshold could never fire for an agent-reported server: the data to
   evaluate it was discarded on the way in.
3. ``PERIOD_CONFIG`` offered 7-day and 30-day windows while
   ``cleanup_old_metrics`` deleted every row after seven days, and two unused
   constants described aggregate retention that no code produced. A 30-day
   chart came back holding at most seven days and said nothing about it.
"""
from datetime import datetime, timedelta

import pytest

from tests.factories import make_server


@pytest.fixture()
def server(app):
    from app import db
    return make_server(db, name='fleet-box', status='online')


def _sample(db, server_id, **fields):
    from app.models.server import ServerMetrics
    row = ServerMetrics(server_id=server_id, timestamp=datetime.utcnow(), **fields)
    db.session.add(row)
    db.session.commit()
    return row


# --- 1. the heartbeat keeps every supported field ---------------------------

def test_a_heartbeat_stores_every_supported_metric_field(app, server):
    """Same producer, same storage contract: nothing is dropped on the way in."""
    from app import db
    from app.models.server import ServerMetrics
    from app.services.agent_registry import agent_registry

    agent_registry.update_heartbeat(server.id, metrics={
        'cpu_percent': 12.5,
        'memory_percent': 40.0,
        'memory_used': 4096,
        'disk_percent': 55.0,
        'disk_used': 8192,
        'network_rx': 1000,
        'network_tx': 2000,
        'network_rx_rate': 11.5,
        'network_tx_rate': 22.5,
        'container_count': 3,
        'container_running': 2,
        'extra': {'load1': 0.4},
    })

    row = (ServerMetrics.query.filter_by(server_id=server.id)
           .order_by(ServerMetrics.timestamp.desc()).first())
    assert row is not None, 'the heartbeat stored no metrics at all'
    assert row.cpu_percent == 12.5
    assert row.memory_used == 4096
    assert row.disk_used == 8192
    assert row.network_rx == 1000
    assert row.network_tx == 2000
    # The two the fleet thresholds actually read.
    assert row.network_rx_rate == 11.5
    assert row.network_tx_rate == 22.5
    assert row.container_running == 2
    assert row.extra == {'load1': 0.4}
    db.session.rollback()


def test_a_zero_reading_survives_the_heartbeat(app, server):
    """0% is a reading. Only ``None`` means "no data"."""
    from app.models.server import ServerMetrics
    from app.services.agent_registry import agent_registry

    agent_registry.update_heartbeat(server.id, metrics={
        'cpu_percent': 0.0, 'network_rx_rate': 0.0, 'container_running': 0,
    })
    row = (ServerMetrics.query.filter_by(server_id=server.id)
           .order_by(ServerMetrics.timestamp.desc()).first())
    assert row.cpu_percent == 0.0
    assert row.network_rx_rate == 0.0
    assert row.container_running == 0


# --- 2. thresholds are evaluated, and somebody is told ----------------------

def _threshold(db, metric='cpu', warning=80.0, critical=95.0, duration=300,
               server_id=None):
    from app.models.metric_alert import ServerAlertThreshold
    row = ServerAlertThreshold(server_id=server_id, metric=metric,
                               warning_threshold=warning,
                               critical_threshold=critical,
                               duration_seconds=duration, enabled=True)
    db.session.add(row)
    db.session.commit()
    return row


def test_a_sustained_breach_opens_an_alert_and_a_recovery_closes_it(app, server):
    from app import db
    from app.models.metric_alert import MetricAlert
    from app.services.fleet_monitor_service import FleetMonitorService

    _threshold(db)
    _sample(db, server.id, cpu_percent=99.0)
    _sample(db, server.id, cpu_percent=97.0)

    out = FleetMonitorService.check_fleet_thresholds()
    assert len(out['opened']) == 1
    alert = MetricAlert.query.filter_by(server_id=server.id, status='active').one()
    assert alert.severity == 'critical'
    assert alert.metric == 'cpu'

    # Evaluating again while it is still breaching must not open a second one.
    _sample(db, server.id, cpu_percent=98.0)
    again = FleetMonitorService.check_fleet_thresholds()
    assert again['opened'] == []
    assert MetricAlert.query.filter_by(server_id=server.id, status='active').count() == 1

    # Back under the threshold: the alert resolves, and the caller is told so
    # it can say so.
    MetricAlert.query.filter_by(server_id=server.id).delete()
    from app.models.server import ServerMetrics
    ServerMetrics.query.filter_by(server_id=server.id).delete()
    db.session.commit()
    _sample(db, server.id, cpu_percent=99.0)
    FleetMonitorService.check_fleet_thresholds()
    ServerMetrics.query.filter_by(server_id=server.id).delete()
    db.session.commit()
    _sample(db, server.id, cpu_percent=5.0)
    recovered = FleetMonitorService.check_fleet_thresholds()
    assert len(recovered['resolved']) == 1
    assert MetricAlert.query.filter_by(server_id=server.id, status='active').count() == 0


def test_a_server_that_stopped_reporting_keeps_its_alert(app, server):
    """No samples in the window is "we do not know", not "it is fine"."""
    from app import db
    from app.models.metric_alert import MetricAlert
    from app.models.server import ServerMetrics
    from app.services.fleet_monitor_service import FleetMonitorService

    _threshold(db)
    _sample(db, server.id, cpu_percent=99.0)
    FleetMonitorService.check_fleet_thresholds()
    assert MetricAlert.query.filter_by(server_id=server.id, status='active').count() == 1

    # Every sample ages out of the duration window; nothing new arrives.
    for row in ServerMetrics.query.filter_by(server_id=server.id).all():
        row.timestamp = datetime.utcnow() - timedelta(hours=2)
    db.session.commit()

    out = FleetMonitorService.check_fleet_thresholds()
    assert out['resolved'] == []
    assert MetricAlert.query.filter_by(server_id=server.id, status='active').count() == 1


def test_the_threshold_check_is_scheduled_and_notifies(app, server, monkeypatch):
    """The missing half: a schedule, and somebody hearing about it."""
    from app import db
    from app.jobs import builtin_handlers

    # It is a registered periodic job, not something waiting for a caller.
    kinds = [row[0] for row in builtin_handlers._BUILTINS]
    assert 'builtin.fleet_thresholds' in kinds

    sent = []

    class _Sdk:
        def send(self, event, to, data=None, **kw):
            sent.append({'event': event, 'to': to, 'data': data or {}, **kw})

    monkeypatch.setattr('app.notifications.sdk.NotifySdk', lambda: _Sdk())

    _threshold(db)
    _sample(db, server.id, cpu_percent=99.0)

    result = builtin_handlers.run_fleet_threshold_checks()
    assert result['opened'] == 1
    assert len(sent) == 1
    assert sent[0]['event'] == 'system.alert'
    assert sent[0]['to'] == 'admins'
    assert sent[0]['severity'] == 'critical'
    # The message names the server and the metric: an operator needs both.
    assert 'fleet-box' in sent[0]['data']['message']
    assert 'cpu' in sent[0]['data']['message']


def test_a_quiet_fleet_notifies_nobody(app, server, monkeypatch):
    from app import db
    from app.jobs import builtin_handlers

    sent = []

    class _Sdk:
        def send(self, *a, **kw):
            sent.append(kw)

    monkeypatch.setattr('app.notifications.sdk.NotifySdk', lambda: _Sdk())
    _threshold(db)
    _sample(db, server.id, cpu_percent=3.0)

    assert builtin_handlers.run_fleet_threshold_checks() is None
    assert sent == []


# --- 3. history says what it can actually serve -----------------------------

def test_history_says_how_far_back_it_can_really_go(app, server):
    from app.services.server_metrics_service import ServerMetricsService

    thirty = ServerMetricsService.get_server_history(server.id, period='30d')
    coverage = thirty['coverage']
    assert coverage['retention_days'] == ServerMetricsService.RAW_RETENTION_DAYS
    # 30 days were asked for; seven days of rows exist to answer with.
    assert coverage['requested_hours'] == 24 * 30
    assert coverage['truncated'] is True
    covers_from = datetime.fromisoformat(coverage['covers_from'])
    assert covers_from > datetime.utcnow() - timedelta(
        days=ServerMetricsService.RAW_RETENTION_DAYS, hours=1)

    # A window inside retention is not truncated.
    day = ServerMetricsService.get_server_history(server.id, period='24h')
    assert day['coverage']['truncated'] is False

    # The aggregated view answers the same question the same way.
    agg = ServerMetricsService.get_aggregated_metrics(server.id, period='30d')
    assert agg['coverage']['truncated'] is True


def test_no_constant_promises_retention_that_nothing_produces(app):
    """The two aggregate-retention constants described a policy that did not
    exist. Either the aggregates get written, or the constants go."""
    from app.services.server_metrics_service import ServerMetricsService

    assert not hasattr(ServerMetricsService, 'HOURLY_RETENTION_DAYS')
    assert not hasattr(ServerMetricsService, 'DAILY_RETENTION_DAYS')


def test_cleanup_deletes_exactly_what_retention_says(app, server):
    from app import db
    from app.models.server import ServerMetrics
    from app.services.server_metrics_service import ServerMetricsService

    keep = _sample(db, server.id, cpu_percent=1.0)
    drop = _sample(db, server.id, cpu_percent=2.0)
    drop.timestamp = datetime.utcnow() - timedelta(
        days=ServerMetricsService.RAW_RETENTION_DAYS + 1)
    db.session.commit()
    # Read the ids before the bulk delete expires the instances.
    keep_id, drop_id = keep.id, drop.id

    deleted = ServerMetricsService.cleanup_old_metrics()
    assert deleted['raw'] >= 1
    remaining = {r.id for r in ServerMetrics.query.filter_by(server_id=server.id).all()}
    assert keep_id in remaining
    assert drop_id not in remaining


def test_aggregated_metrics_work_on_sqlite_too(app, server):
    """``date_trunc`` is PostgreSQL's.

    It was called unconditionally, so this endpoint raised "no such function:
    date_trunc" on every SQLite deployment — and SQLite is one of the two
    supported databases, not a test-only convenience.
    """
    from app import db
    from app.services.server_metrics_service import ServerMetricsService

    _sample(db, server.id, cpu_percent=10.0, memory_percent=20.0)
    _sample(db, server.id, cpu_percent=30.0, memory_percent=40.0)

    hourly = ServerMetricsService.get_aggregated_metrics(
        server.id, period='24h', aggregation='hourly')
    assert hourly['points'] >= 1
    bucket = hourly['data'][0]
    assert bucket['timestamp'], 'the bucket has no timestamp'
    assert bucket['cpu']['avg'] == 20.0
    assert bucket['cpu']['min'] == 10.0
    assert bucket['cpu']['max'] == 30.0
    assert bucket['sample_count'] == 2

    daily = ServerMetricsService.get_aggregated_metrics(
        server.id, period='7d', aggregation='daily')
    assert daily['points'] >= 1


def test_an_escalation_is_not_announced_as_a_recovery(app, server, monkeypatch):
    """warning -> critical closes the warning row. That is not a recovery.

    Both rows end up ``status='resolved'``, so the only thing separating an
    escalation from "it is fine again" is which list the service put it in.
    Getting that wrong tells an operator the problem went away at the moment
    it got worse.
    """
    from app import db
    from app.models.server import ServerMetrics
    from app.jobs import builtin_handlers
    from app.services.fleet_monitor_service import FleetMonitorService

    _threshold(db, warning=50.0, critical=90.0)
    _sample(db, server.id, cpu_percent=60.0)
    first = FleetMonitorService.check_fleet_thresholds()
    assert len(first['opened']) == 1
    assert first['opened'][0].severity == 'warning'

    ServerMetrics.query.filter_by(server_id=server.id).delete()
    db.session.commit()
    _sample(db, server.id, cpu_percent=95.0)

    sent = []

    class _Sdk:
        def send(self, event, to, data=None, **kw):
            sent.append({'severity': kw.get('severity'), 'data': data or {}})

    monkeypatch.setattr('app.notifications.sdk.NotifySdk', lambda: _Sdk())
    result = builtin_handlers.run_fleet_threshold_checks()

    assert result['opened'] == 1
    assert result['superseded'] == 1
    assert result['resolved'] == 0
    # One notification, and it is the escalation.
    assert len(sent) == 1
    assert sent[0]['severity'] == 'critical'
    assert 'recovered' not in sent[0]['data'].get('alert_type', '')
