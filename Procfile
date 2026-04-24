web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
worker: celery -A app.core.celery_app worker --loglevel=info
beat: celery -A app.core.celery_app beat --loglevel=info
