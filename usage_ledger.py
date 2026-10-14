"""Atomic DB reservations. No external network operations within transactions."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from usage_policy import LIMITS, settings, cost

JST = timezone(timedelta(hours=9))


class LimitReached(Exception):
    def __init__(self, message, status=429):
        self.message, self.status = message, status
        super().__init__(message)


class UsageLedger:
    def __init__(self, db, models):
        self.db, self.m = db, models

    @contextmanager
    def transaction(self):
        with Session(self.db.engine, expire_on_commit=False) as session:
            with session.begin():
                control = session.execute(select(self.m['Control']).where(
                    self.m['Control'].id == 1).with_for_update()).scalar_one_or_none()
                if control is None:
                    raise LimitReached('利用制限の初期設定が未完了です。', 503)
                yield session, control

    def reserve(self, user_id, feature, model, input_tokens, idempotency):
        policy = settings()
        daily_limit, input_limit, output_limit = LIMITS[feature]
        if input_tokens > input_limit:
            raise LimitReached('入力が長すぎます。資料や質問を短くしてください。', 413)
        if model not in policy['prices']:
            raise LimitReached('このモデルの料金設定がありません。', 503)
        rates = policy['prices'][model]
        amount = cost(input_limit, output_limit, *rates, policy['fx'])
        now = datetime.now(JST)
        month, day = now.strftime('%Y-%m'), now.strftime('%Y-%m-%d')
        day_key = f'{user_id}:{day}:{feature}'
        dedup = hashlib.sha256(f'{user_id}:{idempotency}'.encode()).hexdigest()
        with self.transaction() as (session, control):
            self._reap(session)
            if control.stopped:
                raise LimitReached('AI機能は一時停止中です。', 503)
            if session.execute(select(self.m['Request'].id).where(self.m['Request'].dedup == dedup)).first():
                raise LimitReached('同じリクエストは既に受け付けています。', 409)
            active = session.execute(select(self.m['Request']).where(self.m['Request'].active.is_(True))).scalars().all()
            if len(active) >= 3 or any(row.user_id == user_id for row in active):
                raise LimitReached('AI処理が実行中です。完了後にお試しください。')
            monthly = session.get(self.m['Month'], month)
            if monthly is None:
                monthly = self.m['Month'](month=month, spent=0, reserved=0, uncertain=0)
                session.add(monthly)
            if monthly.spent + monthly.reserved + monthly.uncertain + amount > policy['stop']:
                raise LimitReached('今月のAI利用予算に達しました。', 503)
            daily = session.get(self.m['Day'], day_key)
            if daily is None:
                daily = self.m['Day'](key=day_key, count=0)
                session.add(daily)
            if daily.count >= daily_limit:
                raise LimitReached('本日の利用上限に達しました。翌日お試しください。')
            daily.count += 1
            monthly.reserved += amount
            if monthly.spent + monthly.reserved + monthly.uncertain >= policy['warning']:
                monthly.warned = True
            row = self.m['Request'](id=str(uuid.uuid4()), user_id=user_id, feature=feature,
                month=month, day_key=day_key, dedup=dedup, status='reserved', active=True,
                expires=time.time() + 300, reservation=amount, model=model,
                input_rate=rates[0], output_rate=rates[1], fx=policy['fx'])
            session.add(row)
            return row.id

    def started(self, request_id):
        with self.transaction() as (session, control):
            row = session.get(self.m['Request'], request_id)
            if control.stopped or row.status != 'reserved':
                raise LimitReached('AI処理を開始できません。', 503)
            row.status = 'started'
            row.expires = time.time() + 300

    def heartbeat(self, request_id):
        with self.transaction() as (session, _):
            row = session.get(self.m['Request'], request_id)
            if row.status != 'started' or not row.active:
                raise LimitReached('AI処理の期限が切れました。', 503)
            row.expires = time.time() + 300

    def settle(self, request_id, input_tokens=None, output_tokens=None, unsent=False):
        with self.transaction() as (session, control):
            row = session.get(self.m['Request'], request_id)
            if row is None or row.status in ('completed', 'cancelled'):
                return
            month = session.get(self.m['Month'], row.month)
            if row.status == 'uncertain':
                if input_tokens is None:
                    return
                month.uncertain -= row.reservation
            else:
                month.reserved -= row.reservation
            if input_tokens is not None and output_tokens is not None:
                row.cost = cost(input_tokens, output_tokens, row.input_rate, row.output_rate, row.fx)
                month.spent += row.cost
                row.input_tokens, row.output_tokens, row.status = input_tokens, output_tokens, 'completed'
                row.active = False
            elif unsent and row.status == 'reserved':
                row.status, row.active = 'cancelled', False
                session.get(self.m['Day'], row.day_key).count -= 1
            else:
                month.uncertain += row.reservation
                row.status = 'uncertain'
                # Keep concurrency occupied until the original execution deadline plus grace.
                row.expires = max(row.expires, time.time() + 60)
            if month.spent + month.reserved + month.uncertain >= settings()['warning']:
                month.warned = True

    def _reap(self, session):
        rows = session.execute(select(self.m['Request']).where(
            self.m['Request'].active.is_(True), self.m['Request'].expires < time.time())).scalars().all()
        for row in rows:
            month = session.get(self.m['Month'], row.month)
            if row.status == 'reserved':
                month.reserved -= row.reservation
                session.get(self.m['Day'], row.day_key).count -= 1
                row.status = 'cancelled'
            elif row.status == 'started':
                month.reserved -= row.reservation
                month.uncertain += row.reservation
                row.status = 'uncertain'
            row.active = False

    def reap(self):
        with self.transaction() as (session, _):
            self._reap(session)

    def stop(self, value):
        with self.transaction() as (_, control):
            control.stopped = value
