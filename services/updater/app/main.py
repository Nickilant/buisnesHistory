import logging
from threading import Lock, Thread
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Header, HTTPException

from .config import settings
from .db import init_db
from .sync_service import run_sync_with_logging, sync_casebook_all, sync_today_and_tomorrow

app = FastAPI(title='Casebook Updater Service')
scheduler = BackgroundScheduler(timezone=ZoneInfo('Europe/Moscow'))
logger = logging.getLogger('uvicorn.error')
_full_sync_lock = Lock()
_full_sync_running = False


def scheduled_sync() -> None:
    run_sync_with_logging('плановое', sync_today_and_tomorrow)


def _run_full_sync_background() -> None:
    global _full_sync_running
    try:
        logger.info('Background full sync started')
        run_sync_with_logging('полное (без фильтра по дате)', sync_casebook_all)
        logger.info('Background full sync finished')
    except Exception:  # pragma: no cover
        logger.exception('Background full sync failed')
    finally:
        with _full_sync_lock:
            _full_sync_running = False


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
        result = run_sync_with_logging('ручное (за сегодня и завтра)', sync_today_and_tomorrow)
        return {'status': 'ok', 'result': result}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post('/sync/full')
def run_full_sync(x_full_sync_secret: str | None = Header(default=None)) -> dict:
    global _full_sync_running
    if not settings.full_sync_secret or x_full_sync_secret != settings.full_sync_secret:
        raise HTTPException(status_code=404, detail='Not found')

    with _full_sync_lock:
        if _full_sync_running:
            return {'status': 'ok', 'result': {'started': False, 'message': 'Полная синхронизация уже выполняется.'}}
        _full_sync_running = True

    Thread(target=_run_full_sync_background, daemon=True).start()
    return {'status': 'ok', 'result': {'started': True, 'message': 'Полная синхронизация запущена в фоне.'}}
