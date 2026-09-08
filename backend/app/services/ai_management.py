"""Validated AI policy and connection-bound Prompture model selection."""
import copy
import math

from app import db
from app.exceptions import ValidationError, PermissionDeniedError
from app.models.system_settings import SystemSettings
from app.services import ai_connections

PROFILES = ('utility', 'standard', 'advanced')
WORKFLOWS = {'chat': 'standard', 'summarize': 'utility', 'extract': 'utility', 'diagnose': 'advanced'}
STRATEGIES = ('cost_optimized', 'balanced', 'quality_first', 'fast')
DEFAULTS = {
    'profiles': {key: None for key in PROFILES},
    'routing_enabled': False, 'strategy': 'balanced', 'pool': [],
    'fallback_enabled': False, 'fallbacks': [], 'budget_policy': 'hard_stop',
    'max_tokens': 0, 'max_output_tokens': 2048, 'max_tool_rounds': 6,
    'max_tool_result_length': 4000, 'max_history_messages': 40,
    'temperature': None, 'reasoning_effort': None,
    'monthly_limit_usd': 0, 'user_monthly_limit_usd': 0,
    'workspace_monthly_limit_usd': 0, 'reservation_usd': 0.5,
}


def settings():
    stored = SystemSettings.get('ai_management', {})
    return {**copy.deepcopy(DEFAULTS), **(stored if isinstance(stored, dict) else {})}


def number(value, label, lower, upper, *, integer=False):
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError(f'{label} must be a number.') from None
    if isinstance(value, bool) or not math.isfinite(n) or not lower <= n <= upper or (integer and n != int(n)):
        raise ValidationError(f'{label} must be between {lower} and {upper}' + (' (whole number).' if integer else '.'))
    return int(n) if integer else n


def binding(data, *, tier=False):
    if not isinstance(data, dict) or set(data) - {'connection_id', 'model', 'tier'}:
        raise ValidationError('Select a saved connection and model.')
    cid, model = data.get('connection_id'), data.get('model')
    if not isinstance(cid, str) or len(cid) > 64 or not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ValidationError('Select a saved connection and an exact model ID.')
    ai_connections.require_connection(cid)
    result = {'connection_id': cid, 'model': model.strip()}
    if tier:
        result['tier'] = data.get('tier', 'standard')
        if result['tier'] not in ('budget', 'standard', 'premium'):
            raise ValidationError('Invalid model capability tier.')
    return result


def validate(data):
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValidationError('Unsupported AI management setting.')
    result = {**settings(), **data}
    profiles = result['profiles']
    if not isinstance(profiles, dict) or set(profiles) != set(PROFILES):
        raise ValidationError('Configure Utility, Standard, and Advanced task models.')
    result['profiles'] = {key: binding(value) if value is not None else None for key, value in profiles.items()}
    for key in ('routing_enabled', 'fallback_enabled'):
        if not isinstance(result[key], bool):
            raise ValidationError(f'{key} must be a boolean.')
    for key in ('pool', 'fallbacks'):
        values = result[key]
        if not isinstance(values, list) or len(values) > 12:
            raise ValidationError('Configure at most 12 candidates.')
        result[key] = [binding(value, tier=key == 'pool') for value in values]
        if len({(v['connection_id'], v['model']) for v in result[key]}) != len(values):
            raise ValidationError('Remove duplicate model candidates.')
    if result['strategy'] not in STRATEGIES or result['budget_policy'] not in ('hard_stop', 'warn_and_continue', 'degrade'):
        raise ValidationError('Invalid routing strategy or budget policy.')
    if result['routing_enabled'] and not result['pool']:
        raise ValidationError('Add approved models before enabling routing.')
    if (result['fallback_enabled'] or result['budget_policy'] == 'degrade') and not result['fallbacks']:
        raise ValidationError('Add fallback models before enabling fallback or degradation.')
    for key, bounds in {
        'max_tokens': (0, 10000000), 'max_output_tokens': (64, 65536),
        'max_tool_rounds': (1, 20), 'max_tool_result_length': (256, 32000),
        'max_history_messages': (4, 200),
    }.items():
        result[key] = number(result[key], key, *bounds, integer=True)
    for key in ('monthly_limit_usd', 'user_monthly_limit_usd', 'workspace_monthly_limit_usd'):
        result[key] = number(result[key], key, 0, 1000000)
    result['reservation_usd'] = number(result['reservation_usd'], 'Per-run reservation', 0.01, 1000)
    if result['temperature'] is not None:
        result['temperature'] = number(result['temperature'], 'Temperature', 0, 2)
    if result['reasoning_effort'] not in (None, 'low', 'medium', 'high'):
        raise ValidationError('Invalid reasoning effort.')
    return result


def save(data):
    result = validate(data)
    SystemSettings.set('ai_management', result, value_type='json')
    db.session.commit()
    return result


def references(connection_id):
    config = settings()
    return any(v and v['connection_id'] == connection_id for v in
               [*config['profiles'].values(), *config['pool'], *config['fallbacks']])


def model_info(connection, model):
    """Exact metadata only: never assign upstream pricing to a gateway alias."""
    from prompture.infra.model_rates import get_model_rates, get_model_info
    result = {'id': model, 'provider': connection.provider, 'pricing': None,
              'context_window': None, 'max_output_tokens': None,
              'tools': None, 'vision': None, 'structured': None, 'reasoning': None,
              'temperature': None, 'reasoning_effort': False}
    if connection.provider in ('openai_compatible', 'prompture-hub', 'lmstudio'):
        return result
    try:
        info = get_model_info(connection.provider, model) or {}
        rates = get_model_rates(connection.provider, model)
        if rates and all(isinstance(rates.get(k), (int, float)) and math.isfinite(rates[k]) and rates[k] >= 0 for k in ('input', 'output')):
            result['pricing'] = {k: rates[k] for k in ('input', 'output')}
        limit = info.get('limit', {})
        result.update(context_window=limit.get('context'), max_output_tokens=limit.get('output'),
                      tools=info.get('tool_call'), vision=('image' in info.get('modalities', {}).get('input', [])) if info else None,
                      structured=info.get('structured_output'), reasoning=info.get('reasoning'),
                      temperature=info.get('temperature'),
                      reasoning_effort=connection.provider == 'openai' and info.get('reasoning') is True)
    except Exception:
        pass  # Missing catalog metadata remains unknown, never free/supported.
    return result


def public_profiles():
    config = settings()
    return {'profiles': config['profiles'], 'routing_enabled': config['routing_enabled'],
            'strategy': config['strategy'], 'workflows': WORKFLOWS}


def resolve(*, profile=None, workflow='chat', connection_id=None, model=None, message='', config=None):
    from prompture.pipeline.resolver import ModelResolver, dict_layer
    from prompture.pipeline.routing import ModelRouter, RoutingConfig
    config = config or settings()
    if not isinstance(workflow, str) or workflow not in WORKFLOWS or (profile is not None and profile not in PROFILES):
        raise ValidationError('Invalid task or model role.')
    requested_profile = profile
    profile = profile or WORKFLOWS[workflow]
    default = ai_connections.get_connection()
    base = {'connection_id': default.id, 'model': default.model}
    choices = {key: value for key, value in config['profiles'].items() if value}
    choices['default'] = choices.get('standard', base)
    # The SDK resolves opaque slot values; the matching map retains credentials.
    key_for = lambda value: value['connection_id'] + ':' + value['model']
    bindings = {key_for(value): value for value in choices.values()}
    layers = [dict_layer({key: key_for(value) for key, value in choices.items()})]
    if connection_id or model:
        connection = ai_connections.get_connection(connection_id or base['connection_id'])
        explicit = binding({'connection_id': connection.id, 'model': model or connection.model})
        bindings[key_for(explicit)] = explicit
        layers.insert(0, dict_layer({profile: key_for(explicit)}))
    resolver = ModelResolver(layers=layers, fallback_slots={key: ['default'] for key in PROFILES})
    selected = dict(bindings[resolver.resolve(profile)])
    strategy, reason = 'explicit', 'Selected task model' if choices.get(profile) else 'Inherited Standard/default model'
    if connection_id or model:
        reason = 'Explicit connection/model override'
    elif config['routing_enabled'] and requested_profile is None:
        candidates = []
        for item in config['pool']:
            connection = ai_connections.get_connection(item['connection_id'])
            if workflow == 'diagnose' and model_info(connection, item['model'])['tools'] is False:
                continue
            candidates.append((ai_connections.model_name(connection, item['model']), item))
        if not candidates:
            raise ValidationError('No approved model supports this task.')
        # Override discovery: never allow the SDK's ambient provider scan.
        class ConnectionRouter(ModelRouter):
            def _get_available_models(self):
                return list(dict.fromkeys(name for name, _ in candidates))

            def _get_model_tier(self, name):
                return next(item['tier'] for candidate, item in candidates if candidate == name)

        router = ConnectionRouter(RoutingConfig(strategy=config['strategy']))
        name, decision = router.select_model(message, {'type': 'object', 'properties': {'answer': {'type': 'string'}}})
        selected = dict(next(item for candidate, item in candidates if candidate == name))
        strategy, reason = config['strategy'], decision.reason
        if strategy == 'fast':
            reason = 'Fast strategy using configured capability tiers (not measured latency)'
    selected.pop('tier', None)
    return {**selected, 'profile': profile, 'workflow': workflow, 'strategy': strategy, 'reason': reason}


def workspace_id(user, requested):
    from app.services.workspace_service import WorkspaceService
    if requested in (None, '', 'all'):
        return None
    resolved = WorkspaceService.resolve_workspace_id(user, requested)
    if resolved is None:
        raise PermissionDeniedError('Workspace is unavailable or access was removed.')
    return resolved


def generation_options(config, connection, model):
    info = model_info(connection, model)
    output = min(config['max_output_tokens'], info['max_output_tokens'] or config['max_output_tokens'])
    options = {'max_tokens': output}
    if config['temperature'] is not None and info['temperature'] is True:
        options['temperature'] = config['temperature']
    if config['reasoning_effort'] and info['reasoning_effort']:
        options['reasoning_effort'] = config['reasoning_effort']
    return options
