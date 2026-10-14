"""Authenticated usage snapshot; all counts and availability are computed in the DB layer."""
from datetime import datetime, timedelta
from flask import jsonify, request, abort
from flask_login import current_user, login_required
from usage_ledger import JST
from usage_policy import LIMITS, settings, cost


def install_usage_view(app, ledger):
    @app.get('/api/ai-usage')
    @login_required
    def ai_usage():
        if request.args:
            abort(400)
        now = datetime.now(JST)
        day, month = now.strftime('%Y-%m-%d'), now.strftime('%Y-%m')
        reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        policy = settings()
        with ledger.transaction() as (session, control):
            monthly = session.get(ledger.m['Month'], month)
            total = monthly.spent + monthly.reserved + monthly.uncertain if monthly else 0
            active = session.query(ledger.m['Request']).filter_by(active=True).all()
            own_active = any(row.user_id == current_user.id for row in active)
            features = {}
            for name, (limit, inputs, outputs) in LIMITS.items():
                daily = session.get(ledger.m['Day'], f'{current_user.id}:{day}:{name}')
                used = daily.count if daily else 0
                model = 'gpt-5.4-mini' if name == 'summary' else 'gpt-5.4-nano'
                rates = policy['prices'].get(model)
                reason = None
                if control.stopped:
                    reason = 'emergency_stop'
                elif rates is None:
                    reason = 'configuration_error'
                elif total + cost(inputs, outputs, *rates, policy['fx']) > policy['stop']:
                    reason = 'budget_stop'
                elif used >= limit:
                    reason = 'daily_limit'
                elif own_active or len(active) >= 3:
                    reason = 'busy'
                features[name] = dict(used=used, limit=limit, remaining=max(0, limit-used),
                                      available=reason is None, reason=reason)
        response = jsonify(day=day, resets_at=reset.isoformat(), features=features)
        response.headers['Cache-Control'] = 'private, no-store'
        return response
