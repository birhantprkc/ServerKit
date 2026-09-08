"""Offline regressions for AI authorization and provider-boundary protection."""
import json
import threading
from types import SimpleNamespace

import pytest

from app import db
from app.services import ai_service
from app.services.ai_tool_registry import ToolDescriptor, ai_tool_registry
from app.services.ai_tools_builtin import register_builtin_tools, tool_caller
from factories import headers_for, make_application, make_server, make_user, make_workspace


@pytest.fixture
def no_pii(monkeypatch):
    monkeypatch.setattr(ai_service, '_pii_enabled', lambda: False)


def descriptor(func, *, write=False):
    return ToolDescriptor('probe', 'probe__probe', func, 'Security probe', {},
                          plugin_slug='probe', is_write=write)


def test_ai_app_list_matches_rest_visibility_and_omits_config(app, client, no_pii):
    from app.services.resource_grant_service import ResourceGrantService
    with app.app_context():
        viewer = make_user(db, role='viewer')
        owner = make_user(db)
        admin = make_user(db, role='admin')
        own = make_application(db, name='own', user_id=viewer.id)
        shared = make_application(db, name='shared', user_id=owner.id)
        foreign = make_application(db, name='foreign', user_id=owner.id)
        deleted = make_application(db, name='deleted', user_id=viewer.id)
        from datetime import datetime
        deleted.deleted_at = datetime.utcnow()
        db.session.commit()
        ResourceGrantService.grant(viewer.id, 'application', shared.id, role='viewer')
        register_builtin_tools()
        d = ai_tool_registry.get('core__list_applications')
        result = ai_service._make_read_wrapper(d, viewer)()
        assert {r['id'] for r in result} == {own.id, shared.id}
        assert all(set(r) == {'id', 'name', 'status', 'app_type', 'port'} for r in result)
        assert client.get(f'/api/v1/apps/{foreign.id}', headers=headers_for(viewer)).status_code == 403
        assert client.get(f'/api/v1/apps/{shared.id}', headers=headers_for(viewer)).status_code == 200
        assert {r['id'] for r in ai_service._make_read_wrapper(d, admin)()} == {
            own.id, shared.id, foreign.id,
        }
        assert tool_caller.get() is None


def test_workspace_membership_does_not_grant_foreign_apps(app, no_pii):
    from app.models.workspace import WorkspaceMember
    with app.app_context():
        viewer = make_user(db, role='viewer')
        owner = make_user(db)
        ws = make_workspace(db, created_by=owner.id)
        db.session.add(WorkspaceMember(workspace_id=ws.id, user_id=viewer.id, role='member'))
        db.session.commit()
        mine = make_application(db, user_id=viewer.id, workspace_id=ws.id)
        make_application(db, user_id=owner.id, workspace_id=ws.id)
        other = make_application(db, user_id=viewer.id)
        register_builtin_tools()
        wrapper = ai_service._make_read_wrapper(ai_tool_registry.get('core__list_applications'), viewer)
        assert {r['id'] for r in wrapper(workspace_id=ws.id)} == {mine.id}
        assert {r['id'] for r in wrapper()} == {mine.id, other.id}


def test_ai_server_list_uses_workspace_scope_and_summary(app, no_pii):
    with app.app_context():
        user = make_user(db, role='admin')
        ws = make_workspace(db, created_by=user.id)
        selected = make_server(db, name='selected', workspace_id=ws.id)
        make_server(db, name='other')
        register_builtin_tools()
        result = ai_service._make_read_wrapper(ai_tool_registry.get('core__list_servers'), user)(workspace_id=ws.id)
        assert [r['id'] for r in result] == [selected.id]
        assert set(result[0]) == {'id', 'name', 'status', 'hostname'}


def test_raw_host_database_and_docker_write_tools_require_admin(app):
    with app.app_context():
        developer = make_user(db)
        register_builtin_tools()
        for name in ('list_databases', 'stop_docker_container', 'restart_docker_container'):
            d = ai_tool_registry.get('core__' + name)
            assert not d.allowed_for(developer)
        assert 'core__stop_docker_container' not in {
            d.qualified_name for d in ai_tool_registry.list_for(developer, 'assistant')
        }


class ApprovingGate:
    def __init__(self, on_approval=lambda: None, cancelled=False):
        self.on_approval = on_approval
        self.cancelled = cancelled
        self.result = None
        self.failed = False

    def request_confirmation(self, d, params):
        self.on_approval()
        return 'approve', 'test-token'

    def is_cancelled(self):
        return self.cancelled

    def mark_executed(self, token, result):
        self.result = result

    def mark_failed(self, token, error):
        self.failed = True


@pytest.mark.parametrize('change', ['disable', 'role', 'session', 'cancel'])
def test_write_rechecks_caller_after_confirmation(app, no_pii, change):
    with app.app_context():
        user = make_user(db, role='admin')
        called = []
        d = descriptor(lambda: called.append(True), write=True)
        d.admin_only = True

        def change_access():
            if change == 'disable':
                user.is_active = False
            elif change == 'role':
                user.role = 'viewer'
            elif change == 'session':
                user.auth_version = 'revoked'
            db.session.commit()

        gate = ApprovingGate(change_access, cancelled=change == 'cancel')
        result = ai_service._make_write_wrapper(d, user, gate)()
        assert not called
        assert 'Permission denied' in result
        assert gate.failed


def test_protected_docker_container_never_restarts(app, monkeypatch, no_pii):
    from app.services.docker_service import DockerService
    with app.app_context():
        user = make_user(db, role='admin')
        called = []
        monkeypatch.setattr(DockerService, 'is_protected_container', lambda cid: True)
        monkeypatch.setattr(DockerService, 'restart_container', lambda cid: called.append(cid))
        register_builtin_tools()
        gate = ApprovingGate()
        ai_service._make_write_wrapper(ai_tool_registry.get('core__restart_docker_container'), user, gate)(container_id='panel')
        assert not called
        assert gate.failed


def test_nested_results_redact_secrets_and_pii(monkeypatch):
    monkeypatch.setattr(ai_service, '_pii_enabled', lambda: True)
    monkeypatch.setattr(ai_service, '_get_pii_redactor', lambda: SimpleNamespace(
        redact=lambda value: SimpleNamespace(text=value.replace('private@example.test', '[email]'))))
    data = {'rows': [{'password': {'value': 'never-send'}, 'email': 'private@example.test',
                      'log': 'password=hidden Authorization: Bearer abcdef'}]}
    result = ai_service._maybe_redact_result(data)
    rendered = json.dumps(result)
    assert all(secret not in rendered for secret in ('never-send', 'private@example.test', 'hidden', 'abcdef'))
    assert data['rows'][0]['password']['value'] == 'never-send'


def test_secret_filter_runs_with_pii_disabled(no_pii):
    for text in ('password=hunter2', 'Authorization: Bearer abcdef',
                 'mysql://root:dbpass@host/db',
                 '-----BEGIN RSA PRIVATE KEY-----\nsecret\n-----END RSA PRIVATE KEY-----'):
        safe = ai_service.redact_input(text)
        assert '[redacted]' in safe
        assert all(secret not in safe for secret in ('hunter2', 'abcdef', 'dbpass', '\nsecret\n'))


def test_read_and_write_result_share_protection(app, no_pii):
    with app.app_context():
        user = make_user(db)
        data = {'items': [{'api_key': 'never-send'}]}
        read = ai_service._make_read_wrapper(descriptor(lambda: data), user)()
        gate = ApprovingGate()
        write = ai_service._make_write_wrapper(descriptor(lambda: data, write=True), user, gate)()
        assert read == write == gate.result == {'items': [{'api_key': '[redacted]'}]}


def test_enabled_protection_failure_never_returns_original(monkeypatch):
    monkeypatch.setattr(ai_service, '_pii_enabled', lambda: True)
    def broken():
        raise RuntimeError('offline')
    monkeypatch.setattr(ai_service, '_get_pii_redactor', broken)
    with pytest.raises(ai_service.AIProtectionError):
        ai_service.redact_input('private@example.test')
    with pytest.raises(ai_service.AIProtectionError):
        ai_service._maybe_redact_result({'email': 'private@example.test'})
    monkeypatch.setattr(ai_service, '_setting', lambda key, default=None: True)
    monkeypatch.setattr(ai_service, '_get_injection_detector', broken)
    with pytest.raises(ai_service.AIProtectionError):
        ai_service.injection_flagged('message')


@pytest.mark.parametrize('route', ['/chat', '/chat/stream'])
def test_chat_bounds_and_visible_protection_failure(client, auth_headers, monkeypatch, route):
    monkeypatch.setattr(ai_service, 'ensure_initialized', lambda: None)
    monkeypatch.setattr(ai_service, 'is_enabled', lambda: True)
    monkeypatch.setattr(ai_service, 'is_configured', lambda: True)
    path = '/api/v1/ai' + route
    invalid = client.post(path, headers=auth_headers, json={'message': 42})
    assert invalid.status_code == 400 and invalid.json['code'] == 'validation_error'
    assert client.post(path, headers=auth_headers, json={'message': 'x' * 16001}).status_code == 400
    oversized = client.post(path, headers=auth_headers, json={'message': 'x' * 140000})
    assert oversized.status_code == 413 and oversized.json['code'] == 'request_entity_too_large'
    def broken(text):
        raise ai_service.AIProtectionError('AI privacy protection is unavailable.')
    monkeypatch.setattr(ai_service, 'injection_flagged', broken)
    response = client.post(path, headers=auth_headers, json={'message': 'hello'})
    assert response.status_code == 503
    assert response.json['code'] == 'dependency_unavailable'
    assert 'protection is unavailable' in response.json['error']


@pytest.mark.parametrize('route', ['/chat', '/chat/stream'])
def test_chat_busy_uses_typed_http_error(client, auth_headers, monkeypatch, route):
    from app.api import ai
    monkeypatch.setattr(ai_service, 'ensure_initialized', lambda: None)
    monkeypatch.setattr(ai_service, 'is_enabled', lambda: True)
    monkeypatch.setattr(ai_service, 'is_configured', lambda: True)
    monkeypatch.setattr(ai_service, 'injection_flagged', lambda text: False)
    monkeypatch.setattr(ai_service, 'redact_input', lambda text: text)
    monkeypatch.setattr(ai, '_claim_turn', lambda uid: False)
    response = client.post('/api/v1/ai' + route, headers=auth_headers, json={'message': 'hello'})
    assert response.status_code == 429
    assert response.json['code'] == 'too_many_requests'
    assert response.json['error'] == 'AI is busy. Wait for the current turn to finish.'
    assert response.json['request_id']


def test_turn_limits_bound_users_and_panel():
    from app.api import ai
    try:
        for uid in range(ai.MAX_ACTIVE_TURNS):
            assert ai._claim_turn(uid)
        assert not ai._claim_turn(0)
        assert not ai._claim_turn(999)
        ai._release_turn(0)
        assert ai._claim_turn(999)
    finally:
        for uid in (*range(ai.MAX_ACTIVE_TURNS), 999):
            ai._release_turn(uid)


def test_cancelled_confirmation_does_not_create_pending_action(app):
    from app.models.ai import AiPendingAction
    with app.app_context():
        cancelled = threading.Event()
        cancelled.set()
        gate = ai_service.ConfirmationGate('unused', 1, lambda *args: None, cancelled, 5)
        assert gate.request_confirmation(descriptor(lambda: None), {}) == ('deny', '')
        assert AiPendingAction.query.count() == 0


def test_stream_tool_rejects_logged_out_session_after_approval(app, no_pii):
    from flask_jwt_extended import decode_token
    from factories import access_token_for
    from app.models import RevokedSession
    with app.app_context():
        user = make_user(db, role='admin')
        claims = decode_token(access_token_for(user))
        called = []

        def logout():
            db.session.add(RevokedSession(session_id=claims['session_id'], user_id=user.id))
            db.session.commit()

        gate = ApprovingGate(logout)
        gate.session_claims = claims
        wrapper = ai_service._make_write_wrapper(descriptor(lambda: called.append(True), write=True), user, gate)
        assert 'Permission denied' in wrapper()
        assert not called
        assert 'Permission denied' in ai_service._make_read_wrapper(
            descriptor(lambda: called.append(True)), user, session_claims=claims)()
        assert not called


def test_service_failure_is_not_recorded_as_success(app, no_pii):
    with app.app_context():
        user = make_user(db)
        gate = ApprovingGate()
        output = {'success': False, 'error': 'Service could not restart'}
        result = ai_service._make_write_wrapper(descriptor(lambda: output, write=True), user, gate)()
        assert result == output
        assert gate.failed and gate.result is None


def test_disconnecting_at_open_cancels_stream_and_releases_slot(client, auth_headers, monkeypatch):
    from app.api import ai
    from prompture.agents.live_events import TextDelta
    released = threading.Event()
    original_release = ai._release_turn

    def release(uid):
        original_release(uid)
        released.set()

    class FakeConversation:
        def ask_live(self, message):
            for _ in range(2000):
                yield TextDelta(text='x')

    monkeypatch.setattr(ai, '_release_turn', release)
    monkeypatch.setattr(ai_service, 'ensure_initialized', lambda: None)
    monkeypatch.setattr(ai_service, 'is_enabled', lambda: True)
    monkeypatch.setattr(ai_service, 'is_configured', lambda: True)
    monkeypatch.setattr(ai_service, 'injection_flagged', lambda message: False)
    monkeypatch.setattr(ai_service, 'redact_input', lambda message: message)
    monkeypatch.setattr(ai_service, 'build_conversation', lambda *args, **kwargs: FakeConversation())
    response = client.post('/api/v1/ai/chat/stream', headers=auth_headers,
                           json={'message': 'hello'}, buffered=False)
    assert response.status_code == 200
    assert 'event: open' in next(iter(response.response)).decode()
    response.close()
    assert released.wait(5), 'Disconnected worker retained a concurrency slot'


def test_old_stream_cleanup_does_not_unregister_new_gate():
    first = ai_service.ConfirmationGate('same-conversation', 1, lambda *args: None, threading.Event(), 5)
    second = ai_service.ConfirmationGate('same-conversation', 1, lambda *args: None, threading.Event(), 5)
    try:
        ai_service.register_gate('same-conversation', first)
        ai_service.register_gate('same-conversation', second)
        ai_service.unregister_gate('same-conversation', first)
        assert ai_service._active_gates['same-conversation'] is second
    finally:
        ai_service.unregister_gate('same-conversation', second)


def test_read_exception_does_not_leak_credentials_to_model(app, no_pii):
    with app.app_context():
        user = make_user(db)
        def broken():
            raise RuntimeError('Cannot connect to mysql://root:secret@host/db')
        result = ai_service._make_read_wrapper(descriptor(broken), user)()
        assert result == 'The tool failed to retrieve data.'


@pytest.mark.parametrize('route', ['/chat', '/chat/stream'])
def test_disabled_assistant_blocks_new_turns(client, auth_headers, monkeypatch, route):
    monkeypatch.setattr(ai_service, 'is_enabled', lambda: False)
    monkeypatch.setattr(ai_service, 'is_configured', lambda: True)
    def unexpected_init():
        pytest.fail('Disabled chat must not initialize a provider or run tools')
    monkeypatch.setattr(ai_service, 'ensure_initialized', unexpected_init)
    response = client.post('/api/v1/ai' + route, headers=auth_headers, json={'message': 'hello'})
    assert response.status_code == 503
    assert 'disabled' in response.json['error']


@pytest.mark.parametrize('route', ['/chat', '/chat/stream'])
def test_budget_stop_is_clear_and_preserves_spend(client, auth_headers, monkeypatch, route):
    from prompture.exceptions import BudgetExceededError
    saved = []

    class BudgetConversation:
        usage = {'cost': 0.5, 'total_tokens': 100}

        def ask(self, message):
            raise BudgetExceededError('Internal provider details must not be returned')

        def ask_live(self, message):
            raise BudgetExceededError('Internal provider details must not be returned')
            yield  # The real streaming SDK raises while iterating.

    monkeypatch.setattr(ai_service, 'ensure_initialized', lambda: None)
    monkeypatch.setattr(ai_service, 'is_enabled', lambda: True)
    monkeypatch.setattr(ai_service, 'is_configured', lambda: True)
    monkeypatch.setattr(ai_service, 'injection_flagged', lambda message: False)
    monkeypatch.setattr(ai_service, 'redact_input', lambda message: message)
    monkeypatch.setattr(ai_service, 'build_conversation', lambda *a, **kw: BudgetConversation())
    monkeypatch.setattr(ai_service, 'persist_conversation', lambda row, conv, **kw: saved.append(conv.usage))
    response = client.post('/api/v1/ai' + route, headers=auth_headers,
                           json={'message': 'Continue'}, buffered=True)
    assert response.status_code == (200 if route.endswith('stream') else 409)
    body = response.get_data(as_text=True)
    assert 'ai_budget_exceeded' in body and 'cost limit' in body
    assert 'Internal provider' not in body
    assert saved == [{'cost': 0.5, 'total_tokens': 100}]
    if route.endswith('stream'):
        assert 'event: done' in body


@pytest.mark.parametrize('value', [-1, 'NaN', 'Infinity', '', None, True])
def test_invalid_cost_limit_cannot_be_saved(client, auth_headers, value):
    response = client.put('/api/v1/ai/settings', headers=auth_headers,
                          json={'max_cost_usd': value})
    assert response.status_code == 400
