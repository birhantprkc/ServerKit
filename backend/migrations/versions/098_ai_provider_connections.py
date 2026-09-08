"""Saved Prompture connections and conversation affinity."""
import json

from alembic import op
import sqlalchemy as sa

revision = '098_ai_provider_connections'
down_revision = '097_user_auth_version'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'ai_provider_connections' not in inspector.get_table_names():
        op.create_table(
            'ai_provider_connections',
            sa.Column('id', sa.String(64), primary_key=True),
            sa.Column('name', sa.String(100), nullable=False),
            sa.Column('provider', sa.String(64), nullable=False),
            sa.Column('model', sa.String(256), nullable=False),
            sa.Column('config_encrypted', sa.Text, nullable=False),
            sa.Column('created_at', sa.DateTime),
            sa.Column('updated_at', sa.DateTime),
        )
    columns = {c['name'] for c in inspector.get_columns('ai_conversations')}
    if 'connection_id' not in columns:
        with op.batch_alter_table('ai_conversations') as batch:
            batch.add_column(sa.Column('connection_id', sa.String(64)))
            batch.create_foreign_key('fk_ai_conversation_connection', 'ai_provider_connections', ['connection_id'], ['id'])
            batch.create_index('ix_ai_conversations_connection_id', ['connection_id'])
    with op.batch_alter_table('ai_conversations') as batch:
        batch.alter_column('model_name', existing_type=sa.String(128), type_=sa.String(384))

    settings = dict(conn.execute(sa.text(
        "SELECT key, value FROM system_settings WHERE key LIKE 'ai_%'"
    )).fetchall())
    provider, model = settings.get('ai_provider'), settings.get('ai_model')
    if not provider or not model:
        return
    if conn.execute(sa.text("SELECT id FROM ai_provider_connections WHERE id = 'legacy'")).first():
        return
    from app.utils.crypto import decrypt_secret, encrypt_secret
    config = {}
    if settings.get('ai_api_key_encrypted'):
        config['api_key'] = decrypt_secret(settings['ai_api_key_encrypted'])
    if settings.get('ai_endpoint'):
        config['base_url' if provider == 'openai' else 'endpoint'] = settings['ai_endpoint']
    conn.execute(sa.text(
        'INSERT INTO ai_provider_connections (id, name, provider, model, config_encrypted, created_at, updated_at) '
        "VALUES ('legacy', 'Existing connection', :provider, :model, :config, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {'provider': provider, 'model': model, 'config': encrypt_secret(json.dumps(config))})
    conn.execute(sa.text(
        "INSERT INTO system_settings (key, value, value_type) SELECT 'ai_default_connection_id', 'legacy', 'string' "
        "WHERE NOT EXISTS (SELECT 1 FROM system_settings WHERE key = 'ai_default_connection_id')"
    ))
    # Only attach conversations from this provider. Older chats from a different
    # provider must be explicitly configured, never routed using the wrong key.
    driver = 'cachibot' if provider == 'prompture-hub' else provider
    conn.execute(sa.text(
        "UPDATE ai_conversations SET connection_id = 'legacy' "
        "WHERE connection_id IS NULL AND model_name LIKE :prefix"
    ), {'prefix': driver + '/%'})


def downgrade():
    with op.batch_alter_table('ai_conversations') as batch:
        batch.drop_column('connection_id')
    op.drop_table('ai_provider_connections')
    op.execute("DELETE FROM system_settings WHERE key = 'ai_default_connection_id'")
