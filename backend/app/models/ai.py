"""AI assistant persistence models (core primitive — powered by Prompture).

Three tables back the in-panel assistant:

- ``AiConversation`` — one chat thread per user. Holds the full Prompture
  ``Conversation.export()`` blob so a thread can be resumed across requests
  (tools are re-supplied on resume; only the message/usage state is stored).
- ``AiMessage`` — denormalized per-turn rows for fast list/transcript rendering
  without re-parsing the export blob (also replays tool-call cards).
- ``AiPendingAction`` — audit/record of a guarded write-tool confirmation. The
  live approve/deny coordination happens in-memory (see ai_service streaming),
  but every request is persisted here for visibility and audit.

JSON columns use the store-as-Text + property pattern from ``plugin.py`` to stay
dialect-agnostic (SQLite/PostgreSQL).
"""
import json
import uuid
from datetime import datetime, timedelta

from app import db
from app.models.json_column_mixin import JsonColumnMixin
from app.models.mixins import TimestampMixin


def _new_id() -> str:
    """Opaque, URL-safe conversation id (also used as Prompture conversation_id)."""
    return uuid.uuid4().hex


class AiConversation(JsonColumnMixin, TimestampMixin, db.Model):
    """A single AI chat thread owned by a user."""
    __tablename__ = 'ai_conversations'

    MODE_ASSISTANT = 'assistant'   # tools + page context
    MODE_SIMPLE = 'simple'         # plain chat, no tools

    id = db.Column(db.String(64), primary_key=True, default=_new_id)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True, nullable=False)
    title = db.Column(db.String(256))
    mode = db.Column(db.String(16), default=MODE_ASSISTANT)
    model_name = db.Column(db.String(384))           # 'provider/model' snapshot at creation
    connection_id = db.Column(db.String(64), db.ForeignKey('ai_provider_connections.id'), index=True)
    export_json = db.Column(db.Text)                 # Prompture conv.export(strip_images=True)
    last_page = db.Column(db.String(256))            # last route the assistant saw (for resume context)
    management_json = db.Column(db.Text)

    @property
    def management(self):
        return self._json_read('management_json', {})

    @management.setter
    def management(self, value):
        self.management_json = json.dumps(value)

    messages = db.relationship(
        'AiMessage', backref='conversation', cascade='all, delete-orphan',
        order_by='AiMessage.created_at', lazy='dynamic',
    )
    pending_actions = db.relationship(
        'AiPendingAction', backref='conversation', cascade='all, delete-orphan', lazy='dynamic',
    )
    # Parent-side backref so the delete-cascade policy covers the NOT NULL FK:
    # a user's conversations die with the account.
    user = db.relationship('User',
                           backref=db.backref('ai_conversations', lazy='dynamic'))

    @property
    def export(self) -> dict:
        return self._json_read('export_json')

    @export.setter
    def export(self, value) -> None:
        self.export_json = json.dumps(value) if value is not None else None

    def to_dict(self, include_messages: bool = False) -> dict:
        data = {
            'id': self.id,
            'user_id': self.user_id,
            'title': self.title or 'New chat',
            'mode': self.mode,
            'model_name': self.model_name,
            'connection_id': self.connection_id,
            'last_page': self.last_page,
            'management': self.management,
            'message_count': self.messages.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_messages:
            data['messages'] = [m.to_dict() for m in self.messages]
        return data


class AiProviderConnection(JsonColumnMixin, TimestampMixin, db.Model):
    """Admin-managed AI connection. All driver configuration is encrypted."""
    __tablename__ = 'ai_provider_connections'

    id = db.Column(db.String(64), primary_key=True, default=_new_id)
    name = db.Column(db.String(100), nullable=False)
    provider = db.Column(db.String(64), nullable=False)
    model = db.Column(db.String(256), nullable=False)
    config_encrypted = db.Column(db.Text, nullable=False)
    model_catalog_json = db.Column(db.Text)
    conversations = db.relationship('AiConversation', backref='connection')

    @property
    def model_catalog(self):
        return self._json_read('model_catalog_json', [])

    @model_catalog.setter
    def model_catalog(self, value):
        self.model_catalog_json = json.dumps(value)

    @property
    def config(self):
        config = self._json_read('_decrypted_config_json', None, expect=dict)
        if config is None:
            raise ValueError('Invalid saved AI connection configuration.')
        return config

    @property
    def _decrypted_config_json(self):
        from app.utils.crypto import decrypt_secret
        return decrypt_secret(self.config_encrypted)

    @config.setter
    def config(self, value):
        from app.utils.crypto import encrypt_secret
        self.config_encrypted = encrypt_secret(json.dumps(value))


class AiMessage(JsonColumnMixin, db.Model):
    """One persisted turn in a conversation (user, assistant, or tool)."""
    __tablename__ = 'ai_messages'

    ROLE_USER = 'user'
    ROLE_ASSISTANT = 'assistant'
    ROLE_TOOL = 'tool'

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.String(64), db.ForeignKey('ai_conversations.id', ondelete='CASCADE'), index=True, nullable=False,
    )
    role = db.Column(db.String(16), nullable=False)
    content = db.Column(db.Text)
    attachments_json = db.Column(db.Text)  # reference manifest; never resolved context
    tool_calls_json = db.Column(db.Text)   # [{id,name,input,output,is_error}] for ToolCallCard replay
    usage_json = db.Column(db.Text)        # {input_tokens, output_tokens, cost}
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def tool_calls(self) -> list:
        return self._json_read('tool_calls_json', [])

    @tool_calls.setter
    def tool_calls(self, value) -> None:
        self.tool_calls_json = json.dumps(value) if value else None

    @property
    def attachments(self) -> list:
        return self._json_read('attachments_json', [])

    @attachments.setter
    def attachments(self, value) -> None:
        self.attachments_json = json.dumps(value) if value else None

    @property
    def usage(self) -> dict:
        return self._json_read('usage_json')

    @usage.setter
    def usage(self, value) -> None:
        self.usage_json = json.dumps(value) if value else None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'conversation_id': self.conversation_id,
            'role': self.role,
            'content': self.content,
            'attachments': self.attachments,
            'tool_calls': self.tool_calls,
            'usage': self.usage,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AiRun(JsonColumnMixin, db.Model):
    """Durable per-turn accounting, retained when a transcript is deleted."""
    __tablename__ = 'ai_runs'

    id = db.Column(db.String(64), primary_key=True, default=_new_id)
    conversation_id = db.Column(db.String(64), db.ForeignKey('ai_conversations.id', ondelete='SET NULL'), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), index=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id', ondelete='SET NULL'), index=True)
    conversation = db.relationship('AiConversation', backref=db.backref('runs', lazy='dynamic'))
    user = db.relationship('User', backref=db.backref('ai_runs', lazy='dynamic'))
    workspace = db.relationship('Workspace', backref=db.backref('ai_runs', lazy='dynamic'))
    connection_id = db.Column(db.String(64), index=True)  # immutable reporting snapshot
    model = db.Column(db.String(384))
    profile = db.Column(db.String(16), nullable=False, default='standard')
    workflow = db.Column(db.String(16), nullable=False, default='chat')
    strategy = db.Column(db.String(32), nullable=False, default='explicit')
    reason = db.Column(db.String(512))
    status = db.Column(db.String(24), nullable=False, default='running', index=True)
    cost = db.Column(db.Float, nullable=False, default=0)
    budget_charge = db.Column(db.Float, nullable=False, default=0)
    reserved_cost = db.Column(db.Float, nullable=False, default=0)
    total_tokens = db.Column(db.Integer, nullable=False, default=0)
    duration_ms = db.Column(db.Float, nullable=False, default=0)
    usage_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = db.Column(db.DateTime)

    @property
    def usage(self):
        return self._json_read('usage_json', {})

    @usage.setter
    def usage(self, value):
        self.usage_json = json.dumps(value)

    def to_dict(self):
        return {**{key: getattr(self, key) for key in (
            'id', 'conversation_id', 'user_id', 'workspace_id', 'connection_id', 'model',
            'profile', 'workflow', 'strategy', 'reason', 'status', 'cost', 'budget_charge',
            'reserved_cost', 'total_tokens', 'duration_ms')},
            'usage': self.usage, 'created_at': self.created_at.isoformat() + 'Z',
            'completed_at': self.completed_at.isoformat() + 'Z' if self.completed_at else None}


class AiPendingAction(JsonColumnMixin, db.Model):
    """A guarded write-tool action awaiting human confirmation.

    Live approve/deny is coordinated in-memory by the streaming worker; this
    row is the durable record (status + result) for audit and admin visibility.
    """
    __tablename__ = 'ai_pending_actions'

    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_DENIED = 'denied'
    STATUS_EXPIRED = 'expired'
    STATUS_EXECUTED = 'executed'
    STATUS_FAILED = 'failed'

    DEFAULT_TTL_SECONDS = 300

    id = db.Column(db.String(64), primary_key=True)   # action token (secrets.token_urlsafe)
    conversation_id = db.Column(
        db.String(64), db.ForeignKey('ai_conversations.id', ondelete='CASCADE'), index=True, nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True, nullable=False)
    # Parent-side backref so the delete-cascade policy covers the NOT NULL FK.
    user = db.relationship('User',
                           backref=db.backref('ai_pending_actions', lazy='dynamic'))
    tool_name = db.Column(db.String(128), nullable=False)   # qualified name, e.g. core__restart_docker_container
    plugin_slug = db.Column(db.String(128))                 # None => built-in tool
    params_json = db.Column(db.Text)
    summary = db.Column(db.Text)
    status = db.Column(db.String(16), default=STATUS_PENDING)
    result_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)

    @property
    def params(self) -> dict:
        return self._json_read('params_json')

    @params.setter
    def params(self, value) -> None:
        self.params_json = json.dumps(value) if value else None

    @property
    def result(self):
        return self._json_read('result_json', None)

    @result.setter
    def result(self, value) -> None:
        self.result_json = json.dumps(value) if value is not None else None

    def is_expired(self) -> bool:
        return self.expires_at is not None and datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        return {
            'action_token': self.id,
            'conversation_id': self.conversation_id,
            'tool_name': self.tool_name,
            'plugin_slug': self.plugin_slug,
            'params': self.params,
            'summary': self.summary,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def make_expiry(cls, ttl_seconds: int | None = None) -> datetime:
        return datetime.utcnow() + timedelta(seconds=ttl_seconds or cls.DEFAULT_TTL_SECONDS)
