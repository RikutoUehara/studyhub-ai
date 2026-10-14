import click
import os
import re
import httpx
from flask import jsonify, request, render_template
from sqlalchemy import event
import time
import json
from email_delivery import send_email

from abuse_control import AbuseControl
from ai_service import AIService
from usage_ledger import UsageLedger, LimitReached
from usage_models import define_usage_models
from usage_policy import settings
from usage_view import install_usage_view


def install_usage(app, db, client):
    os.environ.setdefault('TIKTOKEN_CACHE_DIR', os.path.join(app.root_path, '.cache', 'tiktoken'))
    models = define_usage_models(db)
    # Every initialization starts closed, including accidental development create_all calls.
    event.listen(models['Control'].__table__, 'after_create',
                 lambda target, connection, **kw: connection.execute(target.insert().values(id=1, stopped=True, pdf_ready=False)))
    ledger = UsageLedger(db, models)
    abuse = AbuseControl(ledger)
    app.extensions['usage_ledger'] = ledger
    app.extensions['abuse_control'] = abuse
    install_usage_view(app, ledger)
    settings()

    @app.errorhandler(LimitReached)
    def limit_error(error):
        if request.endpoint in {'login', 'register', 'forgot_password', 'resend_verification'}:
            response = app.make_response(render_template('auth_message.html', message=error.message))
        else:
            response = jsonify(error=error.message, success=False)
        response.status_code = error.status
        if error.status == 429:
            response.headers['Retry-After'] = '60'
        return response

    @app.cli.command('usage-reap')
    def reap():
        ledger.reap()
        with ledger.transaction() as (session, _):
            session.query(models['Bucket']).filter(models['Bucket'].expires < time.time()).delete()
        click.echo('Expired usage reservations processed.')

    @app.cli.command('ai-stop')
    @click.option('--resume', is_flag=True)
    def stop(resume):
        ledger.stop(not resume)
        click.echo('AI resumed.' if resume else 'AI stopped.')

    @app.cli.command('usage-status')
    def status():
        for month in db.session.query(models['Month']).order_by(models['Month'].month.desc()).limit(3):
            click.echo(f'{month.month}: spent={month.spent}, reserved={month.reserved}, uncertain={month.uncertain}, warning={month.warned}')

    @app.cli.command('usage-maintenance')
    @click.pass_context
    def maintenance(ctx):
        """Combine recovery, object cleanup and optional budget alerts in one job."""
        from app import pdf_service
        failed = False
        tasks = [('ai_recovery', lambda: ctx.invoke(reap)),
                 ('pdf_cleanup', lambda: pdf_service['cleanup'](limit=25, detailed=True)),
                 ('budget_notification', lambda: ctx.invoke(notify))]
        for name, action in tasks:
            try:
                result = action()
                if isinstance(result, dict) and result.get('failed', 0):
                    failed = True
                    click.echo(json.dumps({'task': name, 'status': 'failed', **result}))
                else:
                    click.echo(json.dumps({'task': name, 'status': 'success', **(result if isinstance(result, dict) else {})}))
            except Exception as error:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                failed = True
                click.echo(json.dumps({'task': name, 'status': 'failed', 'error_type': type(error).__name__}))
        if failed:
            raise click.ClickException('One or more maintenance tasks failed; see task statuses')

    @app.cli.command('usage-notify')
    def notify():
        destination = os.getenv('AI_ADMIN_EMAIL')
        if not destination:
            raise click.ClickException('AI_ADMIN_EMAIL is required for budget alerts')
        pending = db.session.query(models['Month']).filter_by(warned=True, notified=False).all()
        values = [row.month for row in pending]
        db.session.remove()
        failures = 0
        sent_count = 0
        deferred_count = 0
        for month in values:
            with ledger.transaction() as (session, _):
                row = session.get(models['Month'], month)
                if row.notified:
                    continue
                now = time.time()
                if row.notification_state == 'unknown':
                    failures += 1
                    continue
                if row.notification_started and now - row.notification_started >= 23 * 3600:
                    row.notification_state = 'unknown'
                    failures += 1
                    continue
                if row.notification_lease_until > now:
                    deferred_count += 1
                    continue
                if not row.notification_started:
                    row.notification_started = now
                    row.notification_amount = row.spent + row.reserved + row.uncertain
                row.notification_state = 'sending'
                row.notification_lease_until = now + 60
                amount = str(row.notification_amount)
            try:
                send_email({'from': os.environ['MAIL_FROM'], 'to': [destination],
                          'subject': '単位トルトル君：AI予算注意通知',
                          'text': f'{month} の推定費用（実行中・課金不明分込み）は {amount} 円です。'},
                           'ai-budget-warning-' + month)
                with ledger.transaction() as (session, _):
                    row = session.get(models['Month'], month)
                    row.notified, row.notification_state = True, 'sent'
                sent_count += 1
            except Exception:
                failures += 1
        if failures:
            raise click.ClickException('Budget notifications need retry or delivery review')
        click.echo('Budget notifications processed.')
        return {'sent': sent_count, 'deferred': deferred_count}

    @app.cli.command('usage-init-pdfs')
    def initialize_pdfs():
        """Run during maintenance, while PDF uploads are disabled."""
        from app import Lecture, pdf_service
        from pdf_storage import R2Storage
        with ledger.transaction() as (_, control):
            if control.pdf_ready:
                raise click.ClickException('PDF accounting is already initialized')
        references = [(item.pdf_filename, item.course.user_id) for item in Lecture.query.filter(
            Lecture.pdf_filename.isnot(None)).all()]
        for job in pdf_service['Deletion'].query.all():
            match = re.fullmatch(r'users/(\d+)/lectures/\d+/[^/]+\.pdf', job.key)
            if not match:
                raise click.ClickException('A pending PDF key requires manual review')
            references.append((job.key, int(match.group(1))))
        db.session.remove()
        objects = {}
        store = R2Storage() if references else None
        for key, owner in references:
            if not key.startswith(f'users/{owner}/lectures/'):
                raise click.ClickException('A PDF owner/key mismatch requires manual review')
            try:
                size = store.client.head_object(Bucket=store.bucket, Key=key)['ContentLength']
            except Exception:
                raise click.ClickException('PDF metadata could not be read; initialization cancelled') from None
            objects[key] = (owner, size)
        with ledger.transaction() as (session, control):
            if control.pdf_ready:
                raise click.ClickException('Initialization was already completed by another process')
            for key, (owner, size) in objects.items():
                quota = session.get(models['PdfQuota'], owner)
                if quota is None:
                    quota = models['PdfQuota'](user_id=owner, bytes=0)
                    session.add(quota)
                quota.bytes += size
                session.add(models['PdfObject'](key=key, user_id=owner, bytes=size, status='stored'))
            control.pdf_ready = True
        click.echo(f'Initialized {len(objects)} PDF objects.')

    return AIService(db, ledger, client)
