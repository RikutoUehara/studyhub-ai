"""Shared limits without storing IP addresses, email addresses or content."""
from datetime import datetime
import hashlib
import hmac
import time

from flask import request, current_app
from usage_ledger import JST, LimitReached


class AbuseControl:
    def __init__(self, ledger):
        self.ledger = ledger

    def hash(self, value):
        return hmac.new(current_app.config['SECRET_KEY'].encode(), value.encode(), hashlib.sha256).hexdigest()

    def consume(self, scopes):
        with self.ledger.transaction() as (session, _):
            pending = []
            for namespace, subject, seconds, limit in scopes:
                rolling = namespace == 'mail-cooldown'
                window = 0 if rolling else int((time.time() + (32400 if seconds == 86400 else 0)) // seconds)
                key = f'{namespace}:{self.hash(subject)}:{window}'
                row = session.get(self.ledger.m['Bucket'], key)
                if row is None:
                    row = self.ledger.m['Bucket'](key=key, count=0, last=0)
                    session.add(row)
                if rolling and row.last <= time.time() - seconds:
                    row.count = 0
                if row.count >= limit:
                    raise LimitReached('操作回数の上限に達しました。時間をおいてお試しください。')
                pending.append(row)
            for row in pending:
                row.count += 1
                row.last = time.time()
                row.expires = time.time() + max(seconds, 86400) * 7

    def auth_request(self, endpoint):
        email = request.form.get('email', '').strip().lower()
        if len(email) > 100 or len(request.form.get('password', '')) > 128:
            raise LimitReached('メールアドレスまたはパスワードが長すぎます。', 400)
        # Trust no client-provided forwarding headers. Optional proxy setup is separate.
        ip = request.remote_addr or 'unknown'
        if endpoint == 'login':
            self.consume([('login-account', email, 900, 20), ('login-ip', ip, 900, 300)])
        elif endpoint == 'register':
            self.consume([('register-email', email, 86400, 3), ('register-ip', ip, 3600, 100),
                          ('register-global', 'all', 86400, 300)])
        else:
            self.consume([('mail-request-email', email, 3600, 20), ('mail-request-ip', ip, 3600, 300)])

    def email_send(self, user_id, purpose):
        self.consume([('mail-cooldown', f'{user_id}:{purpose}', 60, 1), ('mail-user-day', str(user_id), 86400, 10),
                      ('mail-global-day', 'all', 86400, 1000)])

    def pdf_reserve(self, user_id, key, size, cleanup_model=None):
        day = datetime.now(JST).strftime('%Y-%m-%d')
        with self.ledger.transaction() as (session, control):
            if not control.pdf_ready:
                raise LimitReached('PDF容量管理の初期化が未完了です。', 503)
            daily_key = f'{user_id}:{day}:pdf'
            daily = session.get(self.ledger.m['Day'], daily_key)
            if daily is None:
                daily = self.ledger.m['Day'](key=daily_key, count=0)
                session.add(daily)
            quota = session.get(self.ledger.m['PdfQuota'], user_id)
            if quota is None:
                quota = self.ledger.m['PdfQuota'](user_id=user_id, bytes=0)
                session.add(quota)
            if daily.count >= 20:
                raise LimitReached('PDFアップロードは1日20回までです。')
            if quota.bytes + size > 100 * 1024 * 1024:
                raise LimitReached('PDF保存容量100 MiBを超えます。不要な授業のPDFを削除してください。')
            daily.count += 1
            quota.bytes += size
            session.add(self.ledger.m['PdfObject'](key=key, user_id=user_id, bytes=size, status='reserved'))
            if cleanup_model is not None:
                job = cleanup_model(key=key, not_before=time.time() + 3600)
                session.add(job)
                session.flush()
                return job.id

    def pdf_deleted(self, key):
        with self.ledger.transaction() as (session, _):
            obj = session.get(self.ledger.m['PdfObject'], key)
            if obj is not None and obj.status != 'deleted':
                session.get(self.ledger.m['PdfQuota'], obj.user_id).bytes -= obj.bytes
                obj.status = 'deleted'
