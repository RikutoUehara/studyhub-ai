from alembic import context
from app import app, db

with app.app_context():
    if context.is_offline_mode():
        context.configure(url=app.config['SQLALCHEMY_DATABASE_URI'], target_metadata=db.metadata,
                          literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        with db.engine.connect() as connection:
            context.configure(connection=connection, target_metadata=db.metadata)
            with context.begin_transaction():
                context.run_migrations()
