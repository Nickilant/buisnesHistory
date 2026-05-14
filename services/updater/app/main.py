import logging
from datetime import datetime
from threading import Lock, Thread
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Header, HTTPException

from .config import settings
from .db import init_db
from .sync_service import run_sync_with_logging, sync_casebook_all, sync_previous_twelve_hours, sync_today_and_tomorrow

app = FastAPI(title='Casebook Updater Service')
SCHEDULER_TIMEZONE = ZoneInfo('Europe/Moscow')
scheduler = BackgroundScheduler(timezone=SCHEDULER_TIMEZONE)
logger = logging.getLogger('uvicorn.error')
_full_sync_lock = Lock()
_scheduled_sync_lock = Lock()
_full_sync_running = False


def scheduled_sync() -> None:
    logger.info('Запуск планового обновления Casebook: пробуем получить lock.')
    if not _scheduled_sync_lock.acquire(blocking=False):
        logger.warning('Плановое обновление пропущено: предыдущий запуск ещё выполняется.')
        return
    try:
        run_sync_with_logging('плановое (за предыдущие 12 часов)', sync_previous_twelve_hours)
    finally:
        _scheduled_sync_lock.release()
        logger.info('Плановое обновление Casebook: lock освобождён.')


def log_scheduler_event(event) -> None:
    if event.code == EVENT_JOB_MISSED:
        logger.warning(
            'Плановое обновление Casebook пропущено планировщиком: job_id=%s, scheduled_run_time=%s.',
            event.job_id,
            event.scheduled_run_time,
        )
        return

    exception = getattr(event, 'exception', None)
    if exception:
        logger.error(
            'Плановое обновление Casebook завершилось ошибкой: job_id=%s, scheduled_run_time=%s, error=%s.',
            event.job_id,
            event.scheduled_run_time,
            exception,
        )
        return

    logger.info(
        'Плановое обновление Casebook выполнено планировщиком: job_id=%s, scheduled_run_time=%s.',
        event.job_id,
        event.scheduled_run_time,
    )


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
    first_run_at = datetime.now(SCHEDULER_TIMEZONE) if settings.scheduler_run_on_startup else None
    logger.info(
        'Настройка планового обновления Casebook: hours_msk=%s, minute_msk=%s, run_on_startup=%s, timezone=%s.',
        settings.scheduler_hours_msk,
        settings.scheduler_minute_msk,
        settings.scheduler_run_on_startup,
        SCHEDULER_TIMEZONE,
    )
    scheduler.add_listener(log_scheduler_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    job = scheduler.add_job(
        scheduled_sync,
        trigger='cron',
        hour=settings.scheduler_hours_msk,
        minute=settings.scheduler_minute_msk,
        second=0,
        next_run_time=first_run_at,
        id='twelve_hour_casebook_sync',
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        'Плановое обновление Casebook запланировано: job_id=%s, next_run_time=%s.',
        job.id,
        job.next_run_time,
    )


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
