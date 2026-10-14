"""Linux production server; stream responses occupy a thread until complete."""
import os

bind = "0.0.0.0:" + os.getenv("PORT", "8000")
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "4"))
timeout = 180
accesslog = None  # Avoid writing password-reset/verification tokens from URL paths.
errorlog = "-"
loglevel = "info"
