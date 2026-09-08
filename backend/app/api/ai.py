"""AI assistant API (core primitive — powered by Prompture).

Routes under ``/api/v1/ai``:
- ``GET  /status``                 capabilities probe (drives the bubble)
- ``GET/PUT /settings``            provider/model/key config (admin; key never returned)
- ``POST /settings/test``          validate a provider/model/key (admin)
- ``GET  /providers`` / ``/models``  settings dropdown data (admin)
- ``GET/POST /conversations``      list / create (current user)
- ``GET/PATCH/DELETE /conversations/<id>``  transcript / rename / delete (owner)
- ``POST /chat``                   non-streaming turn (fallback)
- ``POST /chat/stream``            SSE turn (fetch + ReadableStream; JWT header)
- ``POST /chat/confirm``           approve/deny a guarded write action
- ``POST /chat/cancel``            cancel an in-flight stream
- ``GET  /tools``                  registered-tool introspection (admin, debug)

SSE is driven by a worker thread bridged to the response generator via a
``queue.Queue`` so the model's blocking I/O never stalls the single gevent
worker. The production gevent-websocket worker monkey-patches threading, so the
stdlib ``threading``/``queue`` primitives used here are cooperative; the dev
server uses real threads. Either way the response generator only ever blocks on
``queue.get`` (with a heartbeat timeout), never on the LLM call directly.
"""
from __future__ import annotations

import json
import logging
import queue
import threading

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from flask_jwt_extended import get_jwt, jwt_required
from werkzeug.exceptions import RequestEntityTooLarge, TooManyRequests

from app import db
from app.middleware.rbac import admin_required, get_current_user
from app.models.ai import AiConversation, AiMessage, AiPendingAction
from app.services import ai_service
from app.services import ai_connections
from app.services.ai_attachment_registry import (
    normalize_references,
    resolve_attachments,
)
from app.services.ai_tool_registry import ai_tool_registry
from app.error_reporting import unexpected_response
from app.exceptions import DependencyUnavailableError, ValidationError

logger = logging.getLogger(__name__)
ai_bp = Blueprint('ai', __name__)

HEARTBEAT_SECONDS = 15
MAX_CHAT_BODY_BYTES = 128 * 1024
MAX_MESSAGE_CHARS = 16000
MAX_CONTEXT_CHARS = 16000
MAX_ACTIVE_TURNS = 8
_turn_lock = threading.Lock()
_active_turns = set()


@ai_bp.before_request
def _bound_chat_body():
    if request.endpoint in ('ai.chat', 'ai.chat_stream'):
        if request.content_length and request.content_length > MAX_CHAT_BODY_BYTES:
            raise RequestEntityTooLarge('AI request is too large')
        # Bound chunked bodies too, before JSON parsing allocates the payload.
        request.max_content_length = MAX_CHAT_BODY_BYTES


def _claim_turn(user_id):
    with _turn_lock:
        if user_id in _active_turns or len(_active_turns) >= MAX_ACTIVE_TURNS:
            return False
        _active_turns.add(user_id)
        return True


def _release_turn(user_id):
    with _turn_lock:
        _active_turns.discard(user_id)


def _validate_chat_data(data):
    if not isinstance(data, dict) or not isinstance(data.get('message'), str):
        return 'message must be a string'
    if len(data['message']) > MAX_MESSAGE_CHARS:
        return 'message is too long'
    context = data.get('page_context') or {}
    if not isinstance(context, dict) or len(json.dumps(context)) > MAX_CONTEXT_CHARS:
        return 'page_context must be a small object'
    if data.get('mode', 'assistant') not in ('assistant', 'simple'):
        return 'Invalid assistant mode'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _owned_conversation(conversation_id: str, user):
    """Return the conversation if it exists and belongs to *user*, else None."""
    row = db.session.get(AiConversation, conversation_id)
    if row is None or row.user_id != user.id:
        return None
    return row


def _load_or_create(conversation_id, user, mode, connection_id=None, model=None):
    if conversation_id:
        row = _owned_conversation(conversation_id, user)
        return row  # may be None -> caller 404s
    connection = None
    if connection_id is not None and (not isinstance(connection_id, str) or len(connection_id) > 64):
        raise ValidationError('Invalid connection ID')
    if connection_id or ai_connections.default_id():
        connection = ai_connections.get_connection(connection_id)
    if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 256):
        raise ValidationError('Invalid model ID')
    row = AiConversation(
        user_id=user.id,
        mode=mode or AiConversation.MODE_ASSISTANT,
        model_name=ai_connections.model_name(connection, model) if connection else ai_service.current_model_name(),
        connection_id=connection.id if connection else None,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _attachment_references(data):
    """Validate client references before any conversation row is created."""
    return normalize_references(data.get('attachments'))


# ---------------------------------------------------------------------------
# Status / settings
# ---------------------------------------------------------------------------
@ai_bp.route('/status', methods=['GET'])
@jwt_required()
def status():
    ai_service.ensure_initialized()
    user = get_current_user()
    tools_count = len(ai_tool_registry.list_for(user, 'assistant')) if user else 0
    return jsonify({
        'enabled': ai_service.is_enabled(),
        'configured': ai_service.is_configured(),
        'provider': ai_service._setting('ai_provider', ''),
        'model': ai_service._setting('ai_model', ''),
        'tools_count': tools_count,
        'mode_default': AiConversation.MODE_ASSISTANT,
        'pii_redaction': bool(ai_service._setting('ai_pii_redaction', True)),
        'injection_detection': bool(ai_service._setting('ai_injection_detection', True)),
    })


@ai_bp.route('/settings', methods=['GET'])
@admin_required
def get_settings():
    from app.services.settings_service import SettingsService
    return jsonify({
        'enabled': bool(SettingsService.get('ai_enabled', False)),
        'provider': SettingsService.get('ai_provider', ''),
        'model': SettingsService.get('ai_model', ''),
        'endpoint': SettingsService.get('ai_endpoint', ''),
        'max_cost_usd': SettingsService.get('ai_max_cost_usd', 0.5),
        'fallback_models': SettingsService.get('ai_fallback_models', []),
        'pii_redaction': bool(SettingsService.get('ai_pii_redaction', True)),
        'injection_detection': bool(SettingsService.get('ai_injection_detection', True)),
        # Never return the key itself — only whether one is configured.
        'api_key_set': bool(SettingsService.get('ai_api_key_encrypted', '')),
        'connections': [ai_connections.public_connection(r, details=True)
                        for r in ai_connections.AiProviderConnection.query.order_by(ai_connections.AiProviderConnection.name).all()],
        'default_connection_id': ai_connections.default_id(),
    })


@ai_bp.route('/settings', methods=['PUT'])
@admin_required
def update_settings():
    from app.services.settings_service import SettingsService
    from app.utils.crypto import encrypt_secret

    data = request.get_json(silent=True) or {}
    user = get_current_user()
    uid = user.id if user else None

    field_map = {
        'enabled': ('ai_enabled', bool),
        'provider': ('ai_provider', str),
        'model': ('ai_model', str),
        'endpoint': ('ai_endpoint', str),
        'max_cost_usd': ('ai_max_cost_usd', str),
        'pii_redaction': ('ai_pii_redaction', bool),
        'injection_detection': ('ai_injection_detection', bool),
    }
    for field, (key, caster) in field_map.items():
        if field in data:
            SettingsService.set(key, caster(data[field]), user_id=uid)
    if 'fallback_models' in data and isinstance(data['fallback_models'], list):
        SettingsService.set('ai_fallback_models', data['fallback_models'], user_id=uid)

    # API key: encrypt and store; empty string clears it.
    if 'api_key' in data:
        raw = (data['api_key'] or '').strip()
        SettingsService.set('ai_api_key_encrypted', encrypt_secret(raw) if raw else '', user_id=uid)

    try:
        from app.plugins_sdk import audit
        audit('ai.settings.update', target_type='ai_settings', details={
            'provider': SettingsService.get('ai_provider', ''),
            'model': SettingsService.get('ai_model', ''),
            'api_key_changed': 'api_key' in data,
        })
    except Exception:
        pass
    return jsonify({'ok': True, 'configured': ai_service.is_configured()})


@ai_bp.route('/settings/test', methods=['POST'])
@admin_required
def test_settings():
    from app.services.settings_service import SettingsService
    data = request.get_json(silent=True) or {}
    provider = (data.get('provider') or SettingsService.get('ai_provider', '')).strip()
    model = (data.get('model') or SettingsService.get('ai_model', '')).strip()
    endpoint = data.get('endpoint')
    # Use the posted key if present, else the stored (decrypted) one.
    api_key = data.get('api_key')
    if api_key is None and provider == SettingsService.get('ai_provider', '') and endpoint == SettingsService.get('ai_endpoint', ''):
        api_key = ai_service._decrypted_key()
    if not provider or not model:
        return jsonify({'ok': False, 'error': 'provider and model are required'}), 400
    return jsonify(ai_service.test_settings(provider, model, api_key=api_key, endpoint=endpoint))


@ai_bp.route('/providers', methods=['GET'])
@admin_required
def providers():
    return jsonify({'providers': ai_service.list_providers()})


@ai_bp.route('/connections', methods=['GET'])
@jwt_required()
def connections():
    # The chat selector needs names/models only, never configuration or keys.
    rows = ai_connections.AiProviderConnection.query.order_by(ai_connections.AiProviderConnection.name).all()
    return jsonify({'connections': [ai_connections.public_connection(r) for r in rows],
                    'default_connection_id': ai_connections.default_id()})


@ai_bp.route('/connections', methods=['POST'])
@admin_required
def add_connection():
    try:
        row = ai_connections.save(request.get_json(silent=True))
        return jsonify(ai_connections.public_connection(row, details=True)), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@ai_bp.route('/connections/<connection_id>', methods=['PUT'])
@admin_required
def update_connection(connection_id):
    row = db.session.get(ai_connections.AiProviderConnection, connection_id)
    if row is None:
        return jsonify({'error': 'Connection not found'}), 404
    try:
        row = ai_connections.save(request.get_json(silent=True), row)
        return jsonify(ai_connections.public_connection(row, details=True))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@ai_bp.route('/connections/<connection_id>', methods=['DELETE'])
@admin_required
def delete_connection(connection_id):
    row = db.session.get(ai_connections.AiProviderConnection, connection_id)
    if row is None:
        return jsonify({'error': 'Connection not found'}), 404
    if row.conversations or ai_connections.default_id() == row.id:
        return jsonify({'error': 'Choose another default and delete conversations using this connection before removing it.'}), 409
    db.session.delete(row)
    db.session.commit()
    return jsonify({'ok': True})


@ai_bp.route('/connections/probe', methods=['POST'])
@admin_required
def probe_connection():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Connection must be an object.'}), 400
    existing = None
    if data.get('id'):
        if not isinstance(data['id'], str) or len(data['id']) > 64:
            return jsonify({'error': 'Invalid connection ID'}), 400
        existing = db.session.get(ai_connections.AiProviderConnection, data['id'])
        if existing is None:
            return jsonify({'error': 'Connection not found'}), 404
    try:
        return jsonify(ai_connections.probe(data, existing, test=data.get('test') is True))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@ai_bp.route('/models', methods=['GET'])
@admin_required
def models():
    provider = (request.args.get('provider') or '').strip()
    if not provider:
        return jsonify({'error': 'provider is required'}), 400
    return jsonify(ai_service.list_models(provider))


@ai_bp.route('/tools', methods=['GET'])
@admin_required
def tools():
    ai_service.ensure_initialized()
    return jsonify({'tools': [
        {
            'qualified_name': d.qualified_name, 'name': d.name, 'description': d.description,
            'plugin_slug': d.plugin_slug, 'rbac_feature': d.rbac_feature,
            'rbac_level': d.rbac_level, 'is_write': d.is_write,
            'admin_only': d.admin_only,
        }
        for d in ai_tool_registry.all_descriptors()
    ]})


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
@ai_bp.route('/conversations', methods=['GET'])
@jwt_required()
def list_conversations():
    user = get_current_user()
    rows = (AiConversation.query
            .filter_by(user_id=user.id)
            .order_by(AiConversation.updated_at.desc())
            .limit(100).all())
    return jsonify({'conversations': [r.to_dict() for r in rows]})


@ai_bp.route('/conversations', methods=['POST'])
@jwt_required()
def create_conversation():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    mode = data.get('mode') or AiConversation.MODE_ASSISTANT
    try:
        row = _load_or_create(None, user, mode, data.get('connection_id'), data.get('model'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    row.title = data.get('title')
    db.session.commit()
    return jsonify(row.to_dict()), 201


@ai_bp.route('/conversations/<conversation_id>', methods=['GET'])
@jwt_required()
def get_conversation(conversation_id):
    user = get_current_user()
    row = _owned_conversation(conversation_id, user)
    if row is None:
        return jsonify({'error': 'Conversation not found'}), 404
    return jsonify(row.to_dict(include_messages=True))


@ai_bp.route('/conversations/<conversation_id>', methods=['PATCH'])
@jwt_required()
def rename_conversation(conversation_id):
    user = get_current_user()
    row = _owned_conversation(conversation_id, user)
    if row is None:
        return jsonify({'error': 'Conversation not found'}), 404
    data = request.get_json(silent=True) or {}
    if 'title' in data:
        row.title = (data['title'] or '').strip()[:256] or row.title
        db.session.commit()
    return jsonify(row.to_dict())


@ai_bp.route('/conversations/<conversation_id>', methods=['DELETE'])
@jwt_required()
def delete_conversation(conversation_id):
    user = get_current_user()
    row = _owned_conversation(conversation_id, user)
    if row is None:
        return jsonify({'error': 'Conversation not found'}), 404
    db.session.delete(row)
    db.session.commit()
    try:
        from app.plugins_sdk import audit
        audit('ai.conversation.delete', target_type='ai_conversation', target_id=conversation_id)
    except Exception:
        pass
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Chat (non-streaming fallback)
# ---------------------------------------------------------------------------
@ai_bp.route('/chat', methods=['POST'])
@jwt_required()
def chat():
    ai_service.ensure_initialized()
    user = get_current_user()
    if not ai_service.is_configured():
        return jsonify({'error': 'AI assistant is not configured'}), 503
    data = request.get_json(silent=True) or {}
    invalid = _validate_chat_data(data)
    if invalid:
        raise ValidationError(invalid)
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'message is required'}), 400
    mode = data.get('mode') or AiConversation.MODE_ASSISTANT
    page_context = data.get('page_context') or {}
    # AttachmentValidationError is typed (app.exceptions.ValidationError), so the
    # global handler renders the 400 with a code and request_id.
    attachment_refs = _attachment_references(data)

    try:
        row = _load_or_create(data.get('conversation_id'), user, mode, data.get('connection_id'), data.get('model'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if row is None:
        return jsonify({'error': 'Conversation not found'}), 404

    try:
        if ai_service.injection_flagged(message):
            raise ValidationError('Your message was flagged by the prompt-injection guardrail.')
        safe_message = ai_service.redact_input(message)
    except ai_service.AIProtectionError as exc:
        raise DependencyUnavailableError(str(exc)) from exc

    if not _claim_turn(user.id):
        raise TooManyRequests('AI is busy. Wait for the current turn to finish.')
    try:
        return _run_chat(row, user, mode, page_context, attachment_refs, message, safe_message)
    finally:
        _release_turn(user.id)


def _run_chat(row, user, mode, page_context, attachment_refs, message, safe_message):
    attachment_result = resolve_attachments(user, attachment_refs)
    _persist_user_message(row, message, attachments=attachment_result['manifest'])
    try:
        # gate=None: write tools refuse (no interactive confirmation in this mode).
        conv = ai_service.build_conversation(
            row, user, mode, page_context, gate=None,
            attachment_context=attachment_result['context'],
        )
        reply = conv.ask(safe_message)
    except ai_service.AIProtectionError as exc:
        raise DependencyUnavailableError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return unexpected_response(exc)

    _persist_assistant_message(row, reply, tool_calls=[], usage=conv.usage)
    ai_service.persist_conversation(row, conv, page_context=page_context)
    return jsonify({
        'conversation_id': row.id,
        'reply': reply,
        'usage': conv.usage,
        'attachment_warnings': attachment_result['warnings'],
    })


# ---------------------------------------------------------------------------
# Chat (SSE streaming)
# ---------------------------------------------------------------------------
@ai_bp.route('/chat/stream', methods=['POST'])
@jwt_required()
def chat_stream():
    ai_service.ensure_initialized()
    user = get_current_user()
    if not ai_service.is_configured():
        return jsonify({'error': 'AI assistant is not configured'}), 503
    data = request.get_json(silent=True) or {}
    invalid = _validate_chat_data(data)
    if invalid:
        raise ValidationError(invalid)
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'message is required'}), 400
    mode = data.get('mode') or AiConversation.MODE_ASSISTANT
    page_context = data.get('page_context') or {}
    # AttachmentValidationError is typed (app.exceptions.ValidationError), so the
    # global handler renders the 400 with a code and request_id.
    attachment_refs = _attachment_references(data)

    try:
        row = _load_or_create(data.get('conversation_id'), user, mode, data.get('connection_id'), data.get('model'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if row is None:
        return jsonify({'error': 'Conversation not found'}), 404

    conversation_id = row.id
    user_id = user.id
    app = current_app._get_current_object()
    ttl = int(ai_service._setting('ai_pending_action_ttl_s', 300) or 300)

    # Persist the (original) user message + set title before streaming begins.
    if not row.title:
        row.title = ai_service.derive_title(message)
    attachment_result = resolve_attachments(user, attachment_refs)
    _persist_user_message(row, message, attachments=attachment_result['manifest'])

    try:
        flagged = ai_service.injection_flagged(message)
        safe_message = ai_service.redact_input(message)
    except ai_service.AIProtectionError as exc:
        raise DependencyUnavailableError(str(exc)) from exc
    if not _claim_turn(user_id):
        raise TooManyRequests('AI is busy. Wait for the current turn to finish.')
    claims = dict(get_jwt())

    q: "queue.Queue" = queue.Queue(maxsize=512)
    cancel_event = threading.Event()

    def emit(event_name: str, payload: dict) -> None:
        enqueue(('frame', (event_name, payload)))

    def enqueue(item):
        while not cancel_event.is_set():
            try:
                q.put(item, timeout=0.25)
                return
            except queue.Full:
                continue

    gate = ai_service.ConfirmationGate(conversation_id, user_id, emit, cancel_event, ttl)
    gate.session_claims = claims
    ai_service.register_gate(conversation_id, gate)

    def producer():
        with app.app_context():
            acc_text: list[str] = []
            tool_calls: dict[str, dict] = {}
            tool_order: list[str] = []
            last_usage: dict = {}
            conv = None
            try:
                if flagged:
                    emit('error', {'message': 'Your message was flagged by the prompt-injection guardrail.'})
                    return

                conv_row = db.session.get(AiConversation, conversation_id)
                from app.middleware.session_auth import validate_session_claims
                conv_user = validate_session_claims(claims)
                if conv_user is None or conv_row is None:
                    emit('error', {'message': 'Your session is no longer authorized.'})
                    return
                conv = ai_service.build_conversation(
                    conv_row, conv_user, mode, page_context, gate,
                    attachment_context=attachment_result['context'],
                )
                for event in conv.ask_live(safe_message):
                    if cancel_event.is_set():
                        break
                    name, payload = ai_service.live_event_to_frame(event)
                    if name == 'text_delta':
                        acc_text.append(payload.get('text', ''))
                    elif name == 'tool_use_start':
                        tc = {'id': payload.get('id'), 'name': payload.get('name'),
                              'input': {}, 'output': None, 'is_error': False}
                        tool_calls[payload.get('id')] = tc
                        tool_order.append(payload.get('id'))
                    elif name == 'tool_use_stop':
                        tc = tool_calls.get(payload.get('id'))
                        if tc:
                            tc['input'] = payload.get('input', {})
                            tc['name'] = payload.get('name', tc['name'])
                    elif name == 'tool_result':
                        tc = tool_calls.get(payload.get('id'))
                        if tc is None:
                            tc = {'id': payload.get('id'), 'name': payload.get('name'),
                                  'input': {}, 'output': None, 'is_error': False}
                            tool_calls[payload.get('id')] = tc
                            tool_order.append(payload.get('id'))
                        tc['output'] = payload.get('output')
                        tc['is_error'] = payload.get('is_error', False)
                    elif name in ('message_stop', 'turn_complete'):
                        if payload.get('usage'):
                            last_usage = payload['usage']
                    emit(name, payload)
            except Exception as exc:
                logger.exception("AI stream worker failed")
                emit('error', {'message': str(exc) if isinstance(exc, ai_service.AIProtectionError)
                               else 'AI request failed. Please try again.'})
            finally:
                ai_service.unregister_gate(conversation_id, gate)
                try:
                    text = ''.join(acc_text)
                    if text or tool_order:
                        conv_row = db.session.get(AiConversation, conversation_id)
                        if conv_row is not None:
                            _persist_assistant_message(
                                conv_row, text,
                                tool_calls=[tool_calls[i] for i in tool_order if i in tool_calls],
                                usage=last_usage,
                            )
                            if conv is not None:
                                ai_service.persist_conversation(conv_row, conv, page_context=page_context)
                except Exception:
                    logger.warning("Failed to persist assistant turn", exc_info=True)
                emit('done', {'conversation_id': conversation_id, 'usage': last_usage})
                enqueue(('end', None))
                _release_turn(user_id)

    try:
        threading.Thread(target=producer, daemon=True).start()
    except Exception:
        gate.cancel_all()
        ai_service.unregister_gate(conversation_id, gate)
        _release_turn(user_id)
        raise

    @stream_with_context
    def gen():
        try:
            yield _sse('open', {'conversation_id': conversation_id})
            for warning in attachment_result['warnings']:
                yield _sse('attachment_warning', warning)
            while not cancel_event.is_set():
                try:
                    kind, payload = q.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield ': keepalive\n\n'
                    continue
                if kind == 'end':
                    break
                event_name, data_obj = payload
                yield _sse(event_name, data_obj)
        finally:
            cancel_event.set()
            gate.cancel_all()
            ai_service.unregister_gate(conversation_id, gate)

    return Response(gen(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    })


@ai_bp.route('/chat/confirm', methods=['POST'])
@jwt_required()
def chat_confirm():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    conversation_id = data.get('conversation_id')
    token = data.get('action_token')
    decision = 'approve' if data.get('decision') == 'approve' else 'deny'
    if not conversation_id or not token:
        return jsonify({'error': 'conversation_id and action_token are required'}), 400
    if _owned_conversation(conversation_id, user) is None:
        return jsonify({'error': 'Conversation not found'}), 404
    # Validate the pending action belongs to this user and is still actionable.
    pending = db.session.get(AiPendingAction, token)
    if pending is None or pending.user_id != user.id or pending.conversation_id != conversation_id:
        return jsonify({'error': 'Pending action not found'}), 404
    if pending.status != AiPendingAction.STATUS_PENDING or pending.is_expired():
        return jsonify({'error': 'Pending action is no longer actionable'}), 409
    if not ai_service.resolve_pending(conversation_id, token, decision):
        return jsonify({'error': 'No active stream for this action (it may have expired)'}), 409
    return jsonify({'ok': True, 'decision': decision})


@ai_bp.route('/chat/cancel', methods=['POST'])
@jwt_required()
def chat_cancel():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return jsonify({'error': 'conversation_id is required'}), 400
    if _owned_conversation(conversation_id, user) is None:
        return jsonify({'error': 'Conversation not found'}), 404
    # Deny any pending confirmation, which also unblocks the worker.
    ai_service.resolve_pending(conversation_id, '__cancel__', 'deny')
    with ai_service._gates_lock:
        gate = ai_service._active_gates.get(conversation_id)
    if gate is not None:
        gate.cancel_all()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def _persist_user_message(row, content: str, *, attachments=None) -> None:
    try:
        message = AiMessage(
            conversation_id=row.id,
            role=AiMessage.ROLE_USER,
            content=content,
        )
        message.attachments = attachments or []
        db.session.add(message)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.warning("Failed to persist user message", exc_info=True)


def _persist_assistant_message(row, content: str, *, tool_calls, usage) -> None:
    try:
        msg = AiMessage(conversation_id=row.id, role=AiMessage.ROLE_ASSISTANT, content=content)
        msg.tool_calls = tool_calls or []
        msg.usage = usage or {}
        db.session.add(msg)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.warning("Failed to persist assistant message", exc_info=True)
