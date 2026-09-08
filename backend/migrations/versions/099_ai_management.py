"""Task selection snapshots and durable AI run accounting."""
from alembic import op
import sqlalchemy as sa

revision = '099_ai_management'
down_revision = '098_ai_provider_connections'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'model_catalog_json' not in {c['name'] for c in inspector.get_columns('ai_provider_connections')}:
        op.add_column('ai_provider_connections', sa.Column('model_catalog_json', sa.Text))
    if 'management_json' not in {c['name'] for c in inspector.get_columns('ai_conversations')}:
        op.add_column('ai_conversations', sa.Column('management_json', sa.Text))
    if 'ai_runs' in inspector.get_table_names():
        return
    op.create_table('ai_runs',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('conversation_id', sa.String(64), sa.ForeignKey('ai_conversations.id', ondelete='SET NULL')),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('workspace_id', sa.Integer, sa.ForeignKey('workspaces.id', ondelete='SET NULL')),
        sa.Column('connection_id', sa.String(64)), sa.Column('model', sa.String(384)),
        sa.Column('profile', sa.String(16), nullable=False),
        sa.Column('workflow', sa.String(16), nullable=False),
        sa.Column('strategy', sa.String(32), nullable=False), sa.Column('reason', sa.String(512)),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('cost', sa.Float, nullable=False, server_default='0'),
        sa.Column('budget_charge', sa.Float, nullable=False, server_default='0'),
        sa.Column('reserved_cost', sa.Float, nullable=False, server_default='0'),
        sa.Column('total_tokens', sa.Integer, nullable=False, server_default='0'),
        sa.Column('duration_ms', sa.Float, nullable=False, server_default='0'),
        sa.Column('usage_json', sa.Text), sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('completed_at', sa.DateTime))
    for column in ('conversation_id', 'user_id', 'workspace_id', 'connection_id', 'status', 'created_at'):
        op.create_index('ix_ai_runs_' + column, 'ai_runs', [column])


def downgrade():
    op.drop_table('ai_runs')
    with op.batch_alter_table('ai_conversations') as batch:
        batch.drop_column('management_json')
    with op.batch_alter_table('ai_provider_connections') as batch:
        batch.drop_column('model_catalog_json')
