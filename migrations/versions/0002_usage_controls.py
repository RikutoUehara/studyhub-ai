"""Shared API quotas and PDF accounting; enable only after operator initialization."""
from alembic import op
import sqlalchemy as sa

revision = '0002_usage_controls'
down_revision = '0001_baseline'
NAMES = {'usage_control', 'usage_month', 'usage_day', 'usage_request', 'abuse_bucket', 'pdf_quota', 'pdf_object'}


# Frozen schema: never replace this with live application metadata.
metadata = sa.MetaData()
sa.Table('abuse_bucket', metadata,
    sa.Column('key', sa.String(length=150), primary_key=True, nullable=False),
    sa.Column('count', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('last', sa.Float(), primary_key=False, nullable=False),
    sa.Column('expires', sa.Float(), primary_key=False, nullable=False),
)
sa.Index('ix_abuse_bucket_expires', metadata.tables['abuse_bucket'].c['expires'], unique=False)
sa.Table('pdf_object', metadata,
    sa.Column('key', sa.String(length=255), primary_key=True, nullable=False),
    sa.Column('user_id', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('bytes', sa.BigInteger(), primary_key=False, nullable=False),
    sa.Column('status', sa.String(length=20), primary_key=False, nullable=False),
)
sa.Index('ix_pdf_object_user_id', metadata.tables['pdf_object'].c['user_id'], unique=False)
sa.Table('pdf_quota', metadata,
    sa.Column('user_id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('bytes', sa.BigInteger(), primary_key=False, nullable=False),
)
sa.Table('usage_control', metadata,
    sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('stopped', sa.Boolean(), primary_key=False, nullable=False),
    sa.Column('pdf_ready', sa.Boolean(), primary_key=False, nullable=False),
)
sa.Table('usage_day', metadata,
    sa.Column('key', sa.String(length=100), primary_key=True, nullable=False),
    sa.Column('count', sa.Integer(), primary_key=False, nullable=False),
)
sa.Table('usage_month', metadata,
    sa.Column('month', sa.String(length=7), primary_key=True, nullable=False),
    sa.Column('spent', sa.Numeric(precision=18, scale=6), primary_key=False, nullable=False),
    sa.Column('reserved', sa.Numeric(precision=18, scale=6), primary_key=False, nullable=False),
    sa.Column('uncertain', sa.Numeric(precision=18, scale=6), primary_key=False, nullable=False),
    sa.Column('warned', sa.Boolean(), primary_key=False, nullable=False),
    sa.Column('notified', sa.Boolean(), primary_key=False, nullable=False),
    sa.Column('notification_started', sa.Float(), primary_key=False, nullable=False),
    sa.Column('notification_state', sa.String(length=20), primary_key=False, nullable=False),
    sa.Column('notification_lease_until', sa.Float(), primary_key=False, nullable=False),
    sa.Column('notification_amount', sa.Numeric(precision=18, scale=6), primary_key=False, nullable=True),
)
sa.Table('usage_request', metadata,
    sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
    sa.Column('user_id', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('feature', sa.String(length=20), primary_key=False, nullable=False),
    sa.Column('month', sa.String(length=7), primary_key=False, nullable=False),
    sa.Column('day_key', sa.String(length=100), primary_key=False, nullable=False),
    sa.Column('dedup', sa.String(length=64), primary_key=False, nullable=False),
    sa.Column('status', sa.String(length=20), primary_key=False, nullable=False),
    sa.Column('active', sa.Boolean(), primary_key=False, nullable=False),
    sa.Column('expires', sa.Float(), primary_key=False, nullable=False),
    sa.Column('reservation', sa.Numeric(precision=18, scale=6), primary_key=False, nullable=False),
    sa.Column('cost', sa.Numeric(precision=18, scale=6), primary_key=False, nullable=True),
    sa.Column('input_tokens', sa.Integer(), primary_key=False, nullable=True),
    sa.Column('output_tokens', sa.Integer(), primary_key=False, nullable=True),
    sa.Column('model', sa.String(length=100), primary_key=False, nullable=False),
    sa.Column('input_rate', sa.Numeric(precision=18, scale=6), primary_key=False, nullable=False),
    sa.Column('output_rate', sa.Numeric(precision=18, scale=6), primary_key=False, nullable=False),
    sa.Column('fx', sa.Numeric(precision=18, scale=6), primary_key=False, nullable=False),
    sa.UniqueConstraint('dedup'),
)
sa.Index('ix_usage_request_user_id', metadata.tables['usage_request'].c['user_id'], unique=False)


def upgrade():
    connection = op.get_bind()
    for table in metadata.sorted_tables:
        if table.name in NAMES:
            table.create(connection)
    control = metadata.tables['usage_control']
    connection.execute(control.insert().values(id=1, stopped=True, pdf_ready=False))


def downgrade():
    raise RuntimeError('Usage history must not be deleted by automatic downgrade')
