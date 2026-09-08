"""Prompture-backed chat provider catalog and isolated saved connections.

Prompture imports stay inside functions: the panel does not load the SDK until
someone uses the assistant. Only installed chat drivers appear in the catalog.
"""
import importlib.util
import json
import os
from urllib.parse import urlsplit

from app import db
from app.exceptions import ConflictError, NotFoundError
from app.models.ai import AiProviderConnection
from app.models.system_settings import SystemSettings


# UI metadata complements Prompture's constructor maps (which are not a form
# schema). Never copy secret defaults from Prompture's environment settings.
_DEPENDENCIES = {
    'openai': ['openai'], 'claude': ['anthropic'], 'google': ['google.genai'],
    'azure': ['openai'], 'google_vertexai': ['google.genai', 'anthropic'],
    'bedrock': ['boto3'], 'groq': ['groq'], 'cohere': ['cohere'],
    'mistral': ['mistralai'], 'airllm': ['airllm'],
}
_OPTIONAL_KEYS = {'openai_compatible', 'lmstudio'}


def is_secret(name):
    return any(part in name for part in ('key', 'token', 'secret', 'password'))


def _installed(module):
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def catalog():
    from prompture.drivers.provider_descriptors import PROVIDER_DESCRIPTORS
    from prompture.drivers.openai_compatible_driver import OPENAI_COMPATIBLE_PROFILES
    from prompture.infra.settings import Settings

    result = []
    for descriptor in PROVIDER_DESCRIPTORS:
        if descriptor.alias_for or not descriptor.llm_sync:
            continue
        provider = descriptor.name
        mapping = dict(descriptor.llm_sync.kwarg_map)
        if provider == 'openai_compatible':
            mapping = {'api_key': '', 'endpoint': ''}
        if provider == 'openai':
            mapping['base_url'] = ''
        fields = []
        for name, attr in mapping.items():
            secret = is_secret(name)
            definition = Settings.model_fields.get(attr)
            default = definition.default if definition and not secret else ''
            default = default if isinstance(default, str) else ''
            if name == 'base_url':
                default = 'https://api.openai.com/v1'
            required = secret and provider not in _OPTIONAL_KEYS
            if 'endpoint' in name or name == 'base_url':
                required = True
            if provider in ('azure', 'google_vertexai'):
                required = False  # model-dependent alternatives validated below
            fields.append({
                'name': name, 'label': name.replace('_', ' ').capitalize(),
                'secret': secret, 'required': required, 'default': default,
                'type': 'url' if 'endpoint' in name or name == 'base_url' else 'text',
            })
        missing = [m for m in _DEPENDENCIES.get(provider, []) if not _installed(m)]
        result.append({
            'id': provider, 'label': descriptor.display_name or provider,
            'fields': fields, 'available': not missing,
            'unavailable_reason': f"Missing Python dependencies: {', '.join(missing)}" if missing else '',
            'needs_key': any(f['secret'] and f['required'] for f in fields),
            'supports_endpoint': any(f['type'] == 'url' for f in fields),
            'presets': ([{'id': k, 'endpoint': v['endpoint']} for k, v in OPENAI_COMPATIBLE_PROFILES.items()]
                        if provider == 'openai_compatible' else []),
        })
    hub = next((p for p in result if p['id'] == 'openai_compatible'), None)
    if hub:
        result.append({**hub, 'id': 'prompture-hub', 'label': 'Prompture Hub (self-hosted)', 'presets': []})
    return sorted(result, key=lambda p: p['label'].lower())


def provider_meta(provider):
    meta = next((p for p in catalog() if p['id'] == provider), None)
    if meta is None:
        raise ValueError('Unknown chat provider. Choose an installed Prompture provider.')
    return meta


def public_connection(row, *, details=False):
    data = {'id': row.id, 'name': row.name, 'provider': row.provider, 'model': row.model}
    if details:
        config = row.config
        data['config'] = {k: v for k, v in config.items() if not is_secret(k)}
        data['secrets_set'] = [k for k, v in config.items() if is_secret(k) and v]
    return data


def default_id():
    return SystemSettings.get('ai_default_connection_id', '') or ''


def list_connections(*, details=False):
    rows = AiProviderConnection.query.order_by(AiProviderConnection.name).all()
    return [public_connection(row, details=details) for row in rows]


def require_connection(connection_id):
    row = db.session.get(AiProviderConnection, connection_id)
    if row is None:
        raise NotFoundError('Connection not found')
    return row


def delete_connection(connection_id):
    row = require_connection(connection_id)
    if row.conversations or default_id() == row.id:
        raise ConflictError('Choose another default and delete conversations using this connection before removing it.')
    db.session.delete(row)
    db.session.commit()


def get_connection(connection_id=None):
    selected = connection_id if connection_id is not None else default_id()
    row = db.session.get(AiProviderConnection, selected) if selected else None
    if row is None:
        raise ValueError('Select a saved AI connection in Settings → AI Assistant.')
    return row


def model_name(row, model=None):
    provider = 'openai_compatible' if row.provider in ('prompture-hub', 'lmstudio') else row.provider
    return f'{provider}/{model or row.model}'


def _text(value, label, maximum=2048):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f'{label} must be a string of at most {maximum} characters.')
    return value.strip()


def validate_url(value):
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValueError('Invalid endpoint URL.') from None
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username
            or url.password or url.query or url.fragment or any(c.isspace() for c in value)):
        raise ValueError('Endpoint must be an HTTP(S) URL without credentials, query, or fragment.')
    if port is not None and port < 1:
        raise ValueError('Invalid endpoint port.')
    return value.rstrip('/')


def prepare(data, existing=None):
    """Validate an admin draft; never reuse keys across provider/endpoint changes."""
    if not isinstance(data, dict):
        raise ValueError('Connection must be an object.')
    provider = _text(data.get('provider', ''), 'Provider', 64)
    meta = provider_meta(provider)
    if not meta['available']:
        raise ValueError(meta['unavailable_reason'])
    name = _text(data.get('name', 'Connection test'), 'Name', 100)
    model = _text(data.get('model', ''), 'Model', 256)
    if not name or not model:
        raise ValueError('Connection name and model are required.')
    posted = data.get('config', {})
    if not isinstance(posted, dict):
        raise ValueError('Configuration must be an object.')
    fields = {f['name']: f for f in meta['fields']}
    if set(posted) - set(fields):
        raise ValueError('Unsupported provider configuration field.')
    previous = existing.config if existing and existing.provider == provider else {}
    config = {k: v for k, v in previous.items() if k in fields}
    for key, field in fields.items():
        if key in posted:
            config[key] = _text(posted[key], field['label'], 16384 if field['secret'] else 2048)
        elif key not in config:
            config[key] = field['default']
        if field['type'] == 'url' and config[key]:
            config[key] = validate_url(config[key])
    endpoint_changed = any(f['type'] == 'url' and config.get(k) != (previous.get(k) or f['default']).rstrip('/')
                           for k, f in fields.items())
    if endpoint_changed:
        for key, field in fields.items():
            if field['secret'] and key not in posted:
                config[key] = ''
    for key, field in fields.items():
        if field['required'] and not config.get(key):
            raise ValueError(f"{field['label']} is required. Re-enter credentials when changing an endpoint.")
    if provider == 'azure':
        prefix = 'claude_' if model.startswith('claude') else ('mistral_' if model.startswith(('mistral', 'mixtral')) else '')
        if not config.get(prefix + 'api_key') or not config.get(prefix + 'endpoint'):
            raise ValueError(f'Azure requires {prefix}api_key and {prefix}endpoint for this model.')
    if provider == 'google_vertexai':
        if model.startswith('claude'):
            if not all(config.get(k) for k in ('project_id', 'location', 'access_token')):
                raise ValueError('Vertex Claude requires project ID, location and access token.')
        elif not config.get('api_key'):
            raise ValueError('Vertex Gemini requires an explicit API key; ambient cloud credentials are not used.')
    if provider == 'bedrock' and not config.get('aws_region'):
        raise ValueError('AWS region is required.')
    return {'name': name, 'provider': provider, 'model': model, 'config': config}


def save(data, existing=None):
    draft = prepare(data, existing)
    if 'make_default' in data and not isinstance(data['make_default'], bool):
        raise ValueError('make_default must be a boolean.')
    row = existing or AiProviderConnection()
    # A connection used by a chat keeps its provider/endpoint identity. Credentials
    # may be rotated; changing the destination requires creating a new connection.
    if existing and existing.conversations:
        before = existing.config
        urls = [f for f in provider_meta(draft['provider'])['fields'] if f['type'] == 'url']
        if existing.provider != draft['provider'] or any(
                (before.get(f['name']) or f['default']).rstrip('/') != draft['config'].get(f['name'], '') for f in urls):
            raise ValueError('This connection has conversations. Add a new connection to change its provider or endpoint.')
    for key in ('name', 'provider', 'model', 'config'):
        setattr(row, key, draft[key])
    db.session.add(row)
    db.session.flush()
    if data.get('make_default') or not default_id():
        SystemSettings.set('ai_default_connection_id', row.id)
    db.session.commit()
    return row


def build_driver(row, model=None):
    from prompture.drivers import get_driver_for_model
    from prompture.drivers.provider_descriptors import PROVIDER_DESCRIPTOR_MAP

    # Revalidate saved credentials and required fields; a missing value must not
    # silently resolve through Prompture's process-global environment.
    draft = prepare({'name': row.name, 'provider': row.provider,
                     'model': model or row.model, 'config': row.config})
    provider = row.provider
    config = draft['config']
    if provider == 'claude' and os.environ.get('ANTHROPIC_BASE_URL', '').rstrip('/') not in ('', 'https://api.anthropic.com'):
        raise ValueError('The installed native Anthropic driver inherits ANTHROPIC_BASE_URL. Remove that process override or configure an explicit compatible gateway connection.')
    if provider in ('openai_compatible', 'prompture-hub', 'lmstudio'):
        from prompture.drivers.openai_compatible_driver import OpenAICompatibleDriver

        endpoint = config['endpoint'].removesuffix('/chat/completions')
        # The generic descriptor has no credential kwarg map in 1.11. Construct
        # its driver directly so explicit profile credentials are always passed.
        driver = OpenAICompatibleDriver(
            model=model or row.model, endpoint=endpoint,
            api_key=config.get('api_key') or 'serverkit-no-auth',
        )
        # Block the constructor's ambient-key fallback, then remove the sentinel
        # before any request. Keyless gateways send no Authorization header.
        driver.api_key = config.get('api_key') or None
        return driver
    # Blank overrides suppress factory-level global settings resolution. Required
    # credentials and endpoints above prevent constructor-level fallback as well.
    mapping = PROVIDER_DESCRIPTOR_MAP[provider].llm_sync.kwarg_map
    overrides = {key: config.get(key, '') for key in mapping}
    if provider == 'openai':
        overrides['base_url'] = config['base_url']
    if provider == 'azure':
        for prefix in ('claude_', 'mistral_'):
            overrides[prefix + 'api_key'] = config.get(prefix + 'api_key') or config.get('api_key') or 'serverkit-unconfigured'
            overrides[prefix + 'endpoint'] = config.get(prefix + 'endpoint') or config.get('endpoint') or 'http://127.0.0.1:1'
    driver = get_driver_for_model(model_name(row, model), **overrides)
    if provider == 'azure':
        # Bypass Prompture's global per-model resolver using this instance only.
        prefix = 'claude_' if draft['model'].startswith('claude') else ('mistral_' if draft['model'].startswith(('mistral', 'mixtral')) else '')
        explicit = {'api_key': config[prefix + 'api_key'], 'endpoint': config[prefix + 'endpoint'],
                    'deployment_id': config.get('deployment_id') or draft['model'], 'api_version': '2024-02-15-preview'}
        driver._resolve_model_config = lambda model, options: dict(explicit)
    return driver


def probe(data, existing=None, *, test=False):
    """Bounded network discovery/test with sanitized failures (no SDK tracebacks)."""
    import requests

    draft = prepare({**data, 'name': data.get('name') or 'Connection test',
                     'model': data.get('model') or ('' if test else '_discovery')}, existing)
    provider, config = draft['provider'], draft['config']
    try:
        if provider in ('openai_compatible', 'prompture-hub', 'lmstudio', 'openai'):
            base = (config.get('base_url') or config['endpoint']).removesuffix('/chat/completions')
            headers = {'Authorization': f"Bearer {config['api_key']}"} if config.get('api_key') else {}
            if test:
                response = requests.post(base + '/chat/completions', headers=headers,
                                         json={'model': draft['model'], 'messages': [{'role': 'user', 'content': 'Reply OK.'}],
                                               'max_tokens': 8, 'stream': False},
                                         timeout=(5, 15), allow_redirects=False)
            else:
                response = requests.get(base + '/models', headers=headers, timeout=(5, 10), allow_redirects=False)
            response.raise_for_status()
            if not 200 <= response.status_code < 300:
                raise ValueError('Endpoint redirected. Configure its final URL directly.')
            body = response.json()
            if test:
                if not isinstance(body, dict) or not body.get('choices'):
                    raise ValueError('Endpoint did not return a chat completion.')
                return {'ok': True, 'message': 'Chat completion succeeded.'}
            models = [m['id'] for m in body.get('data', []) if isinstance(m, dict) and isinstance(m.get('id'), str)]
        else:
            from prompture.drivers import PROVIDER_DRIVER_MAP
            cls = PROVIDER_DRIVER_MAP[provider][0]
            lister = getattr(cls, 'list_models', None)
            try:
                models = lister(**config, timeout=10) if callable(lister) else None
            except Exception:
                raise ValueError('Provider model discovery failed. Check credentials and configuration, or enter a model manually.') from None
            if models is None:
                raise ValueError('Model discovery could not be verified. Check credentials and endpoint, or enter a model manually.')
        models = sorted({m for m in models if isinstance(m, str)})
        if test:
            if draft['model'] not in models:
                raise ValueError('Model was not returned by this provider. Check the model ID or verify it in chat.')
            return {'ok': True, 'message': 'Model discovery succeeded. Streaming and tool support depend on the selected model.'}
        return {'models': models, 'source': 'live'}
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 'error'
        raise ValueError(f'Provider returned HTTP {code}. Check credentials, endpoint and model.') from None
    except requests.RequestException:
        raise ValueError('Could not reach the provider within the timeout. Check its address and TLS configuration.') from None
    except (ImportError, ModuleNotFoundError):
        raise ValueError('A required provider dependency is not installed.') from None
    except (TypeError, KeyError, json.JSONDecodeError):
        raise ValueError('Provider returned an unsupported response; enter a model manually.') from None
