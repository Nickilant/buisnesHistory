from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException

from .config import settings
from .db import init_db
from .sync_service import sync_previous_24_hours

app = FastAPI(title='Casebook Updater Service')
scheduler = BackgroundScheduler(timezone=ZoneInfo('Europe/Moscow'))


def scheduled_sync() -> None:
    sync_previous_24_hours()


@app.on_event('startup')
def startup_event() -> None:
    init_db()
    scheduler.add_job(
        scheduled_sync,
        trigger='cron',
        hour=settings.scheduler_hour_msk,
        minute=settings.scheduler_minute_msk,
        id='daily_casebook_sync',
        replace_existing=True,
    )
    scheduler.start()


@app.on_event('shutdown')
def shutdown_event() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/sync/manual')
def run_manual_sync() -> dict:
    try:
        result = sync_previous_24_hours()
        return {'status': 'ok', 'result': result}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc
