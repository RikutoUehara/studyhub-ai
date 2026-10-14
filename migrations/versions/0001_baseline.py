"""Adopt the existing schema only after checking its columns and foreign keys."""
from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa

revision = '0001_baseline'
down_revision = None


# Frozen schema: never replace this with live application metadata.
metadata = sa.MetaData()
sa.Table('pdf_deletion', metadata,
    sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('key', sa.String(length=255), primary_key=False, nullable=False),
    sa.Column('not_before', sa.Float(), primary_key=False, nullable=False),
    sa.UniqueConstraint('key'),
)
sa.Table('user', metadata,
    sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('email', sa.String(length=100), primary_key=False, nullable=False),
    sa.Column('password_hash', sa.String(length=255), primary_key=False, nullable=False),
    sa.UniqueConstraint('email'),
)
sa.Table('course', metadata,
    sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('name', sa.String(length=100), primary_key=False, nullable=False),
    sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), primary_key=False, nullable=False),
)
sa.Table('email_auth', metadata,
    sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), primary_key=True, nullable=False),
    sa.Column('verified', sa.Boolean(), primary_key=False, nullable=False),
    sa.Column('session_version', sa.Integer(), primary_key=False, nullable=False),
)
sa.Table('email_token', metadata,
    sa.Column('digest', sa.String(length=64), primary_key=True, nullable=False),
    sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), primary_key=False, nullable=False),
    sa.Column('purpose', sa.String(length=20), primary_key=False, nullable=False),
    sa.Column('created_at', sa.Float(), primary_key=False, nullable=False),
    sa.Column('expires_at', sa.Float(), primary_key=False, nullable=False),
    sa.Column('used', sa.Boolean(), primary_key=False, nullable=False),
)
sa.Table('lecture', metadata,
    sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('course_id', sa.Integer(), sa.ForeignKey('course.id'), primary_key=False, nullable=False),
    sa.Column('week', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('pdf_filename', sa.String(length=255), primary_key=False, nullable=True),
    sa.Column('summary', sa.Text(), primary_key=False, nullable=True),
    sa.Column('text', sa.Text(), primary_key=False, nullable=True),
)
sa.Table('chat_message', metadata,
    sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('lecture_id', sa.Integer(), sa.ForeignKey('lecture.id'), primary_key=False, nullable=True),
    sa.Column('role', sa.String(length=20), primary_key=False, nullable=True),
    sa.Column('content', sa.Text(), primary_key=False, nullable=True),
)
sa.Table('quiz_attempt', metadata,
    sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('lecture_id', sa.Integer(), sa.ForeignKey('lecture.id'), primary_key=False, nullable=False),
    sa.Column('score', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('total_questions', sa.Integer(), primary_key=False, nullable=False),
    sa.Column('completed_at', sa.DateTime(), primary_key=False, nullable=False),
)


def upgrade():
    connection = op.get_bind()
    names = {'user', 'course', 'lecture', 'chat_message', 'quiz_attempt', 'email_auth', 'email_token', 'pdf_deletion'}
    for table in metadata.sorted_tables:
        if table.name not in names:
            continue
        inspector = inspect(connection)
        if inspector.has_table(table.name):
            actual = {column['name']: column for column in inspector.get_columns(table.name)}
            if set(actual) != set(table.columns.keys()):
                raise RuntimeError('Existing schema differs from baseline: ' + table.name)
            for column in table.columns:
                found = actual[column.name]
                if found['nullable'] != column.nullable or found['type']._type_affinity != column.type._type_affinity:
                    raise RuntimeError('Existing column differs from baseline: ' + table.name + '.' + column.name)
                if getattr(column.type, 'length', None) != getattr(found['type'], 'length', None):
                    raise RuntimeError('Existing column length differs from baseline: ' + table.name)
            expected = {(tuple(f.parent.name for f in constraint.elements), constraint.elements[0].column.table.name,
                         tuple(f.column.name for f in constraint.elements)) for constraint in table.foreign_key_constraints}
            existing = {(tuple(f['constrained_columns']), f['referred_table'], tuple(f['referred_columns']))
                        for f in inspector.get_foreign_keys(table.name)}
            if existing != expected:
                raise RuntimeError('Existing foreign keys differ from baseline: ' + table.name)
            expected_pk = tuple(column.name for column in table.primary_key.columns)
            if tuple(inspector.get_pk_constraint(table.name)['constrained_columns']) != expected_pk:
                raise RuntimeError('Existing primary key differs from baseline: ' + table.name)
            expected_unique = {tuple(column.name for column in constraint.columns)
                               for constraint in table.constraints if constraint.__class__.__name__ == 'UniqueConstraint'}
            actual_unique = {tuple(constraint['column_names']) for constraint in inspector.get_unique_constraints(table.name)}
            if expected_unique != actual_unique:
                raise RuntimeError('Existing unique constraints differ from baseline: ' + table.name)
        else:
            table.create(connection)


def downgrade():
    raise RuntimeError('Baseline downgrade is disabled to protect existing data')
