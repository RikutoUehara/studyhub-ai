"""Usage metadata only; never store request bodies or credentials here."""
def define_usage_models(db):
    class UsageControl(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        stopped = db.Column(db.Boolean, nullable=False, default=False)
        pdf_ready = db.Column(db.Boolean, nullable=False, default=False)

    class UsageMonth(db.Model):
        month = db.Column(db.String(7), primary_key=True)
        spent = db.Column(db.Numeric(18, 6), nullable=False, default=0)
        reserved = db.Column(db.Numeric(18, 6), nullable=False, default=0)
        uncertain = db.Column(db.Numeric(18, 6), nullable=False, default=0)
        warned = db.Column(db.Boolean, nullable=False, default=False)
        notified = db.Column(db.Boolean, nullable=False, default=False)
        notification_started = db.Column(db.Float, nullable=False, default=0)
        notification_state = db.Column(db.String(20), nullable=False, default='pending')
        notification_lease_until = db.Column(db.Float, nullable=False, default=0)
        notification_amount = db.Column(db.Numeric(18, 6))

    class UsageDay(db.Model):
        key = db.Column(db.String(100), primary_key=True)
        count = db.Column(db.Integer, nullable=False, default=0)

    class UsageRequest(db.Model):
        id = db.Column(db.String(36), primary_key=True)
        user_id = db.Column(db.Integer, nullable=False, index=True)
        feature = db.Column(db.String(20), nullable=False)
        month = db.Column(db.String(7), nullable=False)
        day_key = db.Column(db.String(100), nullable=False)
        dedup = db.Column(db.String(64), nullable=False, unique=True)
        status = db.Column(db.String(20), nullable=False)
        active = db.Column(db.Boolean, nullable=False, default=True)
        expires = db.Column(db.Float, nullable=False)
        reservation = db.Column(db.Numeric(18, 6), nullable=False)
        cost = db.Column(db.Numeric(18, 6))
        input_tokens = db.Column(db.Integer)
        output_tokens = db.Column(db.Integer)
        model = db.Column(db.String(100), nullable=False)
        input_rate = db.Column(db.Numeric(18, 6), nullable=False)
        output_rate = db.Column(db.Numeric(18, 6), nullable=False)
        fx = db.Column(db.Numeric(18, 6), nullable=False)

    class AbuseBucket(db.Model):
        key = db.Column(db.String(150), primary_key=True)
        count = db.Column(db.Integer, nullable=False, default=0)
        last = db.Column(db.Float, nullable=False, default=0)
        expires = db.Column(db.Float, nullable=False, default=0, index=True)

    class PdfQuota(db.Model):
        user_id = db.Column(db.Integer, primary_key=True)
        bytes = db.Column(db.BigInteger, nullable=False, default=0)

    class PdfObject(db.Model):
        key = db.Column(db.String(255), primary_key=True)
        user_id = db.Column(db.Integer, nullable=False, index=True)
        bytes = db.Column(db.BigInteger, nullable=False)
        status = db.Column(db.String(20), nullable=False)

    return dict(Control=UsageControl, Month=UsageMonth, Day=UsageDay, Request=UsageRequest,
                Bucket=AbuseBucket, PdfQuota=PdfQuota, PdfObject=PdfObject)
