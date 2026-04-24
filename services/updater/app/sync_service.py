import hashlib
import json
import logging
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from dateutil import parser
from requests import HTTPError
from sqlalchemy import select

from .config import settings
from .db import Case, ContentType, DocumentEvent, SessionLocal

logger = logging.getLogger(__name__)


def _to_dt(value: str | None):
    if not value:
        return None
    parsed = parser.parse(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_hash(item: dict[str, Any]) -> str:
    stable = {
        'case_id': item.get('eventData', {}).get('case', {}).get('id'),
        'document_id': item.get('document', {}).get('id'),
        'findDate': item.get('findDate'),
        'actualDate': item.get('document', {}).get('actualDate'),
        'eventType': item.get('eventType'),
    }
    payload = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _build_casebook_headers() -> dict[str, str]:
    """
    Supports multiple auth styles used by Casebook deployments:
    - legacy headers: apikey/apiversion
    - bearer token in Authorization
    """
    scheme = settings.casebook_auth_scheme.lower()
    headers: dict[str, str] = {}

    if scheme in {'auto', 'apikey'}:
        headers['apikey'] = settings.casebook_api_key
        headers['apiversion'] = settings.casebook_api_version

    if scheme in {'auto', 'bearer'}:
        headers['Authorization'] = f'Bearer {settings.casebook_api_key}'

    return headers


@lru_cache(maxsize=1)
def _resolve_telegram_chat_id() -> str | None:
    if settings.telegram_chat_id:
        return settings.telegram_chat_id
    if not settings.telegram_bot_token:
        return None

    try:
        response = requests.get(
            f'https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates',
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        updates = payload.get('result') or []
        for update in reversed(updates):
            message = update.get('message') or update.get('channel_post') or {}
            chat = message.get('chat') or {}
            chat_id = chat.get('id')
            if chat_id is not None:
                return str(chat_id)
    except Exception:
        logger.exception('Failed to resolve telegram chat_id via getUpdates')

    return None


def _notify_telegram(text: str) -> None:
    if not settings.telegram_bot_token:
        logger.warning('Telegram notifications skipped: TELEGRAM_BOT_TOKEN is not set')
        return

    chat_id = _resolve_telegram_chat_id()
    if not chat_id:
        logger.warning('Telegram notifications skipped: chat_id not found (set TELEGRAM_CHAT_ID or write to bot first)')
        return

    try:
        requests.post(
            f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': text},
            timeout=20,
        ).raise_for_status()
    except Exception:
        # Ошибка уведомлений не должна останавливать синхронизацию.
        logger.exception('Failed to send telegram notification')


def fetch_casebook(start_date: date | None = None, end_date: date | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = None

    while True:
        params = {
            'size': settings.page_size,
        }
        if start_date is not None:
            params['dateFrom'] = start_date.isoformat()
        if end_date is not None:
            params['dateTo'] = end_date.isoformat()
        if offset is not None:
            params['offset'] = offset

        response = requests.get(
            settings.casebook_api_url,
            headers=_build_casebook_headers(),
            params=params,
            timeout=40,
        )
        try:
            response.raise_for_status()
        except HTTPError as exc:
            if response.status_code == 401:
                raise RuntimeError(
                    'Casebook вернул 401 Unauthorized. Проверьте CASEBOOK_API_KEY и схему '
                    'авторизации CASEBOOK_AUTH_SCHEME (auto/apikey/bearer).'
                ) from exc
            raise
        payload = response.json()

        batch = payload.get('items') or []
        items.extend(batch)

        next_offset = payload.get('next')
        if not next_offset:
            break
        offset = next_offset

    return items


def _sync_payload_items(payload_items: list[dict[str, Any]]) -> dict[str, int]:
    inserted = 0
    updated = 0
    skipped = 0

    with SessionLocal() as db:
        for item in payload_items:
            case_obj = item.get('eventData', {}).get('case') or {}
            document_obj = item.get('document') or {}
            content_types = document_obj.get('contentTypes') or []

            case_ext_id = case_obj.get('id')
            case_number = case_obj.get('number')
            if not case_ext_id or not case_number:
                skipped += 1
                continue

            case_db = db.execute(select(Case).where(Case.external_case_id == case_ext_id)).scalar_one_or_none()
            if not case_db:
                case_db = Case(external_case_id=case_ext_id, case_number=case_number)
                db.add(case_db)
                db.flush()
            elif case_db.case_number != case_number:
                case_db.case_number = case_number

            source_hash = _build_hash(item)
            event_db = db.execute(select(DocumentEvent).where(DocumentEvent.source_hash == source_hash)).scalar_one_or_none()
            if event_db:
                updated += 1
                event_db.raw_item = item
                event_db.event_type = item.get('eventType') or event_db.event_type
                event_db.find_date = _to_dt(item.get('findDate'))
                event_db.actual_date = _to_dt(document_obj.get('actualDate'))
                # Refresh content types
                event_db.content_types.clear()
            else:
                event_db = DocumentEvent(
                    case_id=case_db.id,
                    document_external_id=document_obj.get('id'),
                    event_type=item.get('eventType') or 'added',
                    find_date=_to_dt(item.get('findDate')),
                    actual_date=_to_dt(document_obj.get('actualDate')),
                    raw_item=item,
                    source_hash=source_hash,
                )
                db.add(event_db)
                db.flush()
                inserted += 1

            type_name = (document_obj.get('type') or {}).get('name', '').strip()
            for ct in content_types:
                ct_id = ct.get('id')
                ct_name = ct.get('name')
                if not ct_id or not ct_name:
                    continue
                composed_name = f'{type_name} : {ct_name}' if type_name else ct_name
                db.add(
                    ContentType(
                        event_id=event_db.id,
                        content_type_external_id=ct_id,
                        name=composed_name,
                    )
                )

        db.commit()

    return {
        'fetched': len(payload_items),
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
    }


def sync_casebook_range(start_date: date, end_date: date) -> dict[str, int]:
    payload_items = fetch_casebook(start_date, end_date)
    return _sync_payload_items(payload_items)


def sync_casebook_all() -> dict[str, int]:
    payload_items = fetch_casebook()
    return _sync_payload_items(payload_items)


def sync_today_and_tomorrow() -> dict[str, int]:
    today_utc = datetime.now(timezone.utc).date()
    tomorrow_utc = today_utc + timedelta(days=1)
    return sync_casebook_range(today_utc, tomorrow_utc)


def run_sync_with_telegram(sync_kind: str, runner: Any) -> dict[str, int]:
    _notify_telegram(f'Начало обновления данных ({sync_kind}).')
    try:
        result = runner()
        _notify_telegram(
            f'Завершено обновление данных ({sync_kind}). '
            f'Получено: {result["fetched"]}, добавлено: {result["inserted"]}, '
            f'обновлено: {result["updated"]}, пропущено: {result["skipped"]}.'
        )
        return result
    except Exception:
        _notify_telegram(f'Ошибка обновления данных ({sync_kind}).')
        raise
