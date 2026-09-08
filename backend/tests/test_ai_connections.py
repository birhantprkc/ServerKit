"""Provider discovery, credential boundaries, migration, and real SDK wire format."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import db
from app.models.ai import AiConversation, AiProviderConnection
from app.models.system_settings import SystemSettings
from app.services import ai_connections as service, ai_service


def draft(**overrides):
    return {'name': 'OmniRoute', 'provider': 'openai_compatible', 'model': 'anthropic/team/model',
            'config': {'endpoint': 'http://gateway.local:20128/v1', 'api_key': 'private-test-key'}, **overrides}


@pytest.mark.parametrize('raw', ['', '{broken', '[]', 'null'])
def test_corrupt_saved_config_fails_closed(app, raw):
    from app.utils.crypto import encrypt_secret
    with app.app_context():
        row = AiProviderConnection(config_encrypted=encrypt_secret(raw))
        with pytest.raises(ValueError, match='Invalid saved AI connection'):
            _ = row.config


def test_connection_errors_use_shared_contract(client, auth_headers):
    for method in ('put', 'delete'):
        response = getattr(client, method)('/api/v1/ai/connections/missing',
                                           json={}, headers=auth_headers)
        assert response.status_code == 404
        assert response.get_json()['code'] == 'not_found'
    response = client.post('/api/v1/ai/connections', json={}, headers=auth_headers)
    assert response.status_code == 400
    assert response.get_json()['code'] == 'validation_error'


def test_connection_catalog_supports_api_key_clients_without_secrets(app, client, auth_headers):
    from app.models import User
    from app.services.api_key_service import ApiKeyService
    created = client.post('/api/v1/ai/connections', json=draft(), headers=auth_headers)
    assert created.status_code == 201
    with app.app_context():
        viewer = User(username='connection_viewer', email='connection-viewer@test.local',
                      password_hash='unused', role=User.ROLE_VIEWER, is_active=True)
        db.session.add(viewer)
        db.session.commit()
        _, key = ApiKeyService.create_key(viewer.id, name='connection selector', scopes=['*'])
    headers = {'X-API-Key': key}
    response = client.get('/api/v1/ai/connections', headers=headers)
    assert response.status_code == 200
    assert set(response.get_json()['connections'][0]) == {'id', 'name', 'provider', 'model', 'model_catalog'}
    assert 'private-test-key' not in response.get_data(as_text=True)
    assert client.post('/api/v1/ai/connections', json=draft(), headers=headers).status_code == 403


def test_catalog_uses_prompture_descriptors_and_deduplicates():
    from prompture.drivers.provider_descriptors import PROVIDER_DESCRIPTORS
    catalog = service.catalog()
    names = [p['id'] for p in catalog]
    assert len(names) == len(set(names))
    assert {d.name for d in PROVIDER_DESCRIPTORS if d.llm_sync and not d.alias_for} <= set(names)
    assert {'claude', 'google', 'openai', 'openai_compatible', 'bedrock', 'mistral'} <= set(names)
    assert service.provider_meta('openai_compatible')['presets']


def test_catalog_reports_missing_dependencies(monkeypatch):
    monkeypatch.setattr(service, '_installed', lambda module: False)
    assert service.provider_meta('claude')['available'] is False
    with pytest.raises(ValueError, match='dependencies'):
        service.prepare(draft(provider='claude', config={'api_key': 'key'}))


@pytest.mark.parametrize('url', ['file:///tmp/a', 'http://user:pass@host/v1', 'https://host/v1?key=x',
                                  'http://host/#fragment', 'http://host:99999', 'http://host/a b'])
def test_invalid_endpoint(url):
    with pytest.raises(ValueError):
        service.prepare(draft(config={'endpoint': url}))


def test_profile_storage_and_api_never_return_secrets(app, client, auth_headers):
    response = client.post('/api/v1/ai/connections', json=draft(), headers=auth_headers)
    assert response.status_code == 201
    data = response.get_json()
    assert data['secrets_set'] == ['api_key']
    assert 'api_key' not in data['config']
    with app.app_context():
        row = db.session.get(AiProviderConnection, data['id'])
        assert 'private-test-key' not in row.config_encrypted
        assert row.config['api_key'] == 'private-test-key'
        assert service.default_id() == row.id
    for path in ('/api/v1/ai/connections', '/api/v1/ai/settings'):
        body = client.get(path, headers=auth_headers).get_data(as_text=True)
        assert 'private-test-key' not in body
    selector = client.get('/api/v1/ai/connections', headers=auth_headers).get_json()['connections'][0]
    assert set(selector) == {'id', 'name', 'provider', 'model', 'model_catalog'}


def test_secret_retention_clear_and_endpoint_change(app):
    with app.app_context():
        row = service.save(draft())
        same = service.prepare(draft(config={'endpoint': 'http://gateway.local:20128/v1'}), row)
        assert same['config']['api_key'] == 'private-test-key'
        changed = service.prepare(draft(config={'endpoint': 'http://different.local/v1'}), row)
        assert changed['config']['api_key'] == ''
        cleared = service.prepare(draft(config={'api_key': ''}), row)
        assert cleared['config']['api_key'] == ''
        with pytest.raises(ValueError, match='required'):
            service.prepare(draft(provider='claude', config={}), row)


def test_native_fields_and_global_credentials_are_not_used(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-secret')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://unintended.local/v1')
    with pytest.raises(ValueError, match='required'):
        service.prepare(draft(provider='openai', config={}))
    prepared = service.prepare(draft(provider='openai', config={'api_key': 'explicit'}))
    row = SimpleNamespace(**prepared)
    driver = service.build_driver(row)
    assert driver.api_key == 'explicit'
    assert str(driver.client.base_url) == 'https://api.openai.com/v1/'
    driver.client.close()


def test_native_anthropic_driver_uses_explicit_credentials(monkeypatch):
    monkeypatch.setenv('CLAUDE_API_KEY', 'ambient-secret')
    row = SimpleNamespace(**service.prepare(draft(provider='claude', model='claude-test', config={'api_key': 'explicit-claude'})))
    driver = service.build_driver(row)
    assert driver.api_key == 'explicit-claude'
    assert driver.model == 'claude-test'


def test_probe_uses_draft_credentials_preserves_ids_and_rejects_failure(app, monkeypatch):
    import requests
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {'data': [{'id': 'anthropic/team/model'}, {'id': 'auto'}]})
    monkeypatch.setattr(requests, 'get', get)
    with app.app_context():
        row = service.save(draft())
        result = service.probe(draft(config={'endpoint': 'http://other.local/v1', 'api_key': 'new-key'}), row)
    assert result['models'] == ['anthropic/team/model', 'auto']
    assert calls[0][1]['headers']['Authorization'] == 'Bearer new-key'
    assert calls[0][1]['timeout'] == (5, 10)
    assert calls[0][1]['allow_redirects'] is False
    def fail(*args, **kwargs):
        raise requests.Timeout('private-test-key')
    monkeypatch.setattr(requests, 'get', fail)
    with pytest.raises(ValueError, match='timeout') as exc:
        service.probe(draft())
    assert 'private-test-key' not in str(exc.value)


def test_connection_test_requires_real_response(monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: SimpleNamespace(
        status_code=200, raise_for_status=lambda: None, json=lambda: {}))
    with pytest.raises(ValueError, match='chat completion'):
        service.probe(draft(), test=True)


def test_chats_keep_connection_and_model_and_block_destination_changes(app, client, auth_headers):
    first = client.post('/api/v1/ai/connections', json=draft(), headers=auth_headers).get_json()
    chat = client.post('/api/v1/ai/conversations', json={'connection_id': first['id'], 'model': 'auto/fast'}, headers=auth_headers)
    assert chat.status_code == 201
    second = client.post('/api/v1/ai/connections', json=draft(name='Second', make_default=True), headers=auth_headers).get_json()
    with app.app_context():
        row = db.session.get(AiConversation, chat.get_json()['id'])
        assert row.connection_id == first['id']
        assert row.model_name == 'openai_compatible/auto/fast'
        assert service.default_id() == second['id']
    response = client.put('/api/v1/ai/connections/' + first['id'], json=draft(config={'endpoint': 'http://other/v1'}), headers=auth_headers)
    assert response.status_code == 400
    assert client.delete('/api/v1/ai/connections/' + first['id'], headers=auth_headers).status_code == 409


@pytest.mark.parametrize('provider', ['openai_compatible', 'prompture-hub', 'lmstudio'])
@pytest.mark.parametrize('api_key', ['', 'explicit-gateway-key'])
def test_compatible_driver_streaming_and_tool_protocol_preserve_model(monkeypatch, provider, api_key):
    import requests
    from prompture.drivers.openai_compatible_driver import OpenAICompatibleDriver
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'ambient-secret')
    monkeypatch.setattr(service, '_installed', lambda module: False)
    model = 'fireworks/team/model'
    row = SimpleNamespace(**service.prepare(draft(provider=provider, model=model,
        config={'endpoint': 'http://gateway.local/v1/chat/completions', 'api_key': api_key})))
    driver = service.build_driver(row)
    assert type(driver) is OpenAICompatibleDriver
    calls = []
    closed = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def raise_for_status(self):
            pass

        def json(self):
            return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'peek', 'arguments': '{}'}}]}}],
                'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}}

        def iter_lines(self, **kwargs):
            if self.payload.get('tools'):
                deltas = [
                    {'tool_calls': [{'index': 0, 'id': 'call1', 'type': 'function', 'function': {'name': 'peek', 'arguments': '{'}}]},
                    {'tool_calls': [{'index': 0, 'function': {'arguments': '}'}}]},
                ]
            else:
                deltas = [{'content': 'OK'}]
            for delta in deltas:
                yield 'data: ' + json.dumps({'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})
            yield 'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if self.payload.get('tools') else 'stop'}],
                'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}})
            yield 'data: [DONE]'

    def post(url, **kwargs):
        calls.append((url, kwargs['headers'], kwargs['json']))
        return Response(kwargs['json'])

    monkeypatch.setattr(requests, 'post', post)
    assert driver.supports_streaming and driver.supports_tool_use and driver.supports_streaming_tool_use
    messages = [{'role': 'user', 'content': 'Hi'}]
    tools = [{'type': 'function', 'function': {'name': 'peek', 'parameters': {'type': 'object', 'properties': {}}}}]
    chunks = list(driver.generate_messages_stream(messages, {}))
    assert any(c.get('text') == 'OK' for c in chunks)
    assert chunks[-1]['meta']['total_tokens'] == 5
    result = driver.generate_messages_with_tools(messages, tools, {})
    assert result['tool_calls'][0]['name'] == 'peek'
    events = list(driver.generate_messages_with_tools_stream(messages, tools, {}))
    assert {'tool_use_start', 'tool_input_delta', 'tool_use_stop', 'message_stop'} <= {e.event_type for e in events}
    assert next(e for e in events if e.event_type == 'tool_use_stop').input == {}
    assert len(closed) == 2
    assert all(url == 'http://gateway.local/v1/chat/completions' for url, _, _ in calls)
    assert all(payload['model'] == model for _, _, payload in calls)
    for _, headers, _ in calls:
        assert headers.get('Authorization') == (f'Bearer {api_key}' if api_key else None)
        assert 'ambient-secret' not in str(headers)
    assert calls[-1][2]['tools'][0]['function']['name'] == 'peek'


def test_resume_restores_history_with_original_connection_not_ambient(app, monkeypatch):
    from prompture import Conversation
    from prompture.drivers.base import Driver
    calls = []
    class FakeDriver(Driver):
        def generate(self, prompt, options):
            return {'text': 'OK', 'meta': {}}
    monkeypatch.setattr(ai_service, 'build_tool_registry', lambda *a: None)
    monkeypatch.setattr(ai_service, 'build_system_prompt', lambda *a, **kw: 'Current policy')
    monkeypatch.setattr(ai_service, '_maybe_redact_result', lambda data: data)
    monkeypatch.setattr(service, 'build_driver', lambda row, model=None: calls.append((row.id, model, row.config['api_key'])) or FakeDriver())
    with app.app_context():
        first = service.save(draft())
        row = SimpleNamespace(id='chat', connection_id=first.id, model_name='openai_compatible/auto', export=None)
        fresh = ai_service.build_conversation(row, None, 'simple', {}, None)
        fresh._messages = [{'role': 'user', 'content': 'Remember me'}]
        row.export = fresh.export()
        service.save(draft(name='Other', make_default=True, config={'endpoint': 'http://other/v1', 'api_key': 'different'}))
        resumed = ai_service.build_conversation(row, None, 'simple', {}, None)
        assert isinstance(resumed, Conversation)
        assert resumed._messages[0]['content'] == 'Remember me'
        assert calls == [(first.id, 'auto', 'private-test-key')] * 2
        assert resumed._fallback_models is None
        assert 'private-test-key' not in json.dumps(resumed.export())


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('resume', [False, True])
def test_cost_limit_stops_driver_calls_in_fresh_and_resumed_chats(app, monkeypatch, streaming, resume):
    from prompture.drivers.base import Driver
    from prompture.exceptions import BudgetExceededError

    class NoCallDriver(Driver):
        def generate(self, prompt, options):
            pytest.fail('A conversation at its cost limit must not call a provider')

    monkeypatch.setattr(ai_service, 'build_tool_registry', lambda *a: None)
    monkeypatch.setattr(ai_service, 'build_system_prompt', lambda *a, **kw: 'Policy')
    monkeypatch.setattr(ai_service, '_maybe_redact_result', lambda data: data)
    monkeypatch.setattr(service, 'build_driver', lambda *a: NoCallDriver())
    with app.app_context():
        SystemSettings.set('ai_max_cost_usd', '0.5')
        connection = service.save(draft())
        row = SimpleNamespace(id='budget-chat', connection_id=connection.id,
                              model_name='openai_compatible/auto', export=None)
        conv = ai_service.build_conversation(row, None, 'simple', {}, None)
        conv._usage['cost'] = 0.5
        if resume:
            row.export = conv.export()
            conv = ai_service.build_conversation(row, None, 'simple', {}, None)
        with pytest.raises(BudgetExceededError):
            if streaming:
                list(conv.ask_live('Continue'))
            else:
                conv.ask('Continue')
        assert conv.budget_remaining['exceeded'] is True
        # Removing the ceiling must also apply when resuming saved policy.
        row.export = conv.export()
        SystemSettings.set('ai_max_cost_usd', '0')
        assert ai_service.build_conversation(row, None, 'simple', {}, None).budget_remaining is None


def test_migration_preserves_legacy_key_and_conversation(app):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.utils.crypto import encrypt_secret
    path = Path(__file__).parents[1] / 'migrations/versions/098_ai_provider_connections.py'
    spec = importlib.util.spec_from_file_location('migration098', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with app.app_context():
        SystemSettings.set('ai_provider', 'openai')
        SystemSettings.set('ai_model', 'gpt-test')
        SystemSettings.set('ai_api_key_encrypted', encrypt_secret('legacy-key'))
        db.session.commit()
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()  # startup schema sync / rerun is safe
        db.session.expire_all()
        row = db.session.get(AiProviderConnection, 'legacy')
        assert row.config['api_key'] == 'legacy-key'
        assert service.default_id() == 'legacy'
        # Legacy OpenAI records predate the explicit base_url field. Editing the
        # name must not count the official default URL as a destination change.
        prepared = service.prepare({'name': 'Renamed', 'provider': 'openai', 'model': 'gpt-test', 'config': {}}, row)
        assert prepared['config']['api_key'] == 'legacy-key'


def test_migration_from_old_schema_adds_connection_foreign_key():
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).parents[1] / 'migrations/versions/098_ai_provider_connections.py'
    spec = importlib.util.spec_from_file_location('migration098_old', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE system_settings (key TEXT, value TEXT, value_type TEXT)'))
        conn.execute(sa.text('CREATE TABLE ai_conversations (id TEXT PRIMARY KEY, model_name VARCHAR(128))'))
        conn.execute(sa.text("INSERT INTO ai_conversations VALUES ('old', 'claude/old-model')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        inspector = sa.inspect(conn)
        assert 'ai_provider_connections' in inspector.get_table_names()
        assert any(c['name'] == 'connection_id' for c in inspector.get_columns('ai_conversations'))
        assert inspector.get_foreign_keys('ai_conversations')[0]['referred_table'] == 'ai_provider_connections'
        assert conn.execute(sa.text("SELECT model_name FROM ai_conversations WHERE id = 'old'")).scalar() == 'claude/old-model'
    engine.dispose()
