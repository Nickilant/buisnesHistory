import hashlib
import json
import logging
from time import sleep
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dateutil import parser
from requests import HTTPError, RequestException
from sqlalchemy import select

from .config import settings
from .db import Case, ContentType, DocumentEvent, SessionLocal

logger = logging.getLogger('uvicorn.error')
CASEBOOK_DATE_TIMEZONE = ZoneInfo('Europe/Moscow')
CASE_DOCUMENT_SNAPSHOT_SOURCE = 'case_document_snapshot'


def _format_casebook_date_param(value: date | datetime) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(CASEBOOK_DATE_TIMEZONE)
        return value.replace(tzinfo=None, microsecond=0).isoformat(timespec='seconds')
    return value.isoformat()


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
    if item.get('_source'):
        stable['source'] = item.get('_source')
    payload = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'true', '1', 'yes', 'y'}
    return bool(value)


def _clean_document_value(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _document_named_part(document: dict[str, Any], key: str, field: str) -> str | None:
    value = (document.get(key) or {}).get(field)
    return _clean_document_value(value)


def _compose_document_type_name(document: dict[str, Any]) -> str | None:
    type_name = _document_named_part(document, 'type', 'name')
    decision_type_name = _document_named_part(document, 'decisionType', 'name')

    name_parts: list[str] = []
    for name in (type_name, decision_type_name):
        if name and name not in name_parts:
            name_parts.append(name)

    return ': '.join(name_parts) if name_parts else None


def _compose_document_type_id(document: dict[str, Any]) -> str | None:
    type_id = _document_named_part(document, 'type', 'id')
    decision_type_id = _document_named_part(document, 'decisionType', 'id')

    id_parts: list[str] = []
    for id_part in (type_id, decision_type_id):
        if id_part and id_part not in id_parts:
            id_parts.append(id_part)

    return ':'.join(id_parts) if id_parts else None


def _build_casebook_headers() -> dict[str, str]:
    """Build Casebook auth headers.

    Do not send several auth schemes at once: Casebook can return a successful
    but empty response when `apikey`, `apiversion`, and `Authorization` are
    mixed for the tracking endpoint.  The default `auto` mode intentionally
    mirrors the documented curl example for this endpoint: only `apikey`.
    """
    scheme = settings.casebook_auth_scheme.lower()
    headers: dict[str, str] = {'accept': 'application/json'}

    if scheme in {'auto', 'apikey'}:
        headers['apikey'] = settings.casebook_api_key
    elif scheme in {'apikey_versioned', 'apikey-versioned', 'legacy'}:
        headers['apikey'] = settings.casebook_api_key
        headers['apiversion'] = settings.casebook_api_version
    elif scheme == 'bearer':
        headers['Authorization'] = f'Bearer {settings.casebook_api_key}'
    else:
        raise RuntimeError(
            'Неизвестная схема авторизации CASEBOOK_AUTH_SCHEME. '
            'Допустимые значения: auto, apikey, apikey_versioned, bearer.'
        )

    return headers


def _retry_delay_seconds(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            value = float(retry_after)
            if value > 0:
                return min(value, settings.casebook_retry_max_delay_seconds)
        except ValueError:
            pass

    delay = settings.casebook_retry_base_delay_seconds * (2 ** attempt)
    return min(delay, settings.casebook_retry_max_delay_seconds)


def _is_retryable_casebook_status(status_code: int) -> bool:
    return status_code in {403, 429} or status_code >= 500


def fetch_casebook(start_date: date | datetime | None = None, end_date: date | datetime | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = None
    page_number = 0

    while True:
        params = {
            'size': settings.page_size,
        }
        if start_date is not None:
            params['dateFrom'] = _format_casebook_date_param(start_date)
        if end_date is not None:
            params['dateTo'] = _format_casebook_date_param(end_date)
        if offset is not None:
            params['offset'] = offset

        logger.info('Запрос Casebook: params=%s.', params)
        response = None
        for attempt in range(settings.casebook_retry_attempts + 1):
            try:
                response = requests.get(
                    settings.casebook_api_url,
                    headers=_build_casebook_headers(),
                    params=params,
                    timeout=40,
                )
                response.raise_for_status()
                break
            except HTTPError as exc:
                if response is not None and response.status_code == 401:
                    raise RuntimeError(
                        'Casebook вернул 401 Unauthorized. Проверьте CASEBOOK_API_KEY и схему '
                        'авторизации CASEBOOK_AUTH_SCHEME (auto/apikey/apikey_versioned/bearer).'
                    ) from exc

                status_code = response.status_code if response is not None else None
                is_retryable = status_code is not None and _is_retryable_casebook_status(status_code)
                if is_retryable and attempt < settings.casebook_retry_attempts:
                    retry_after = response.headers.get('Retry-After') if response is not None else None
                    delay = _retry_delay_seconds(attempt, retry_after)
                    logger.warning(
                        'Casebook %s для params=%s. Повтор через %.1f сек (попытка %s/%s).',
                        status_code,
                        params,
                        delay,
                        attempt + 1,
                        settings.casebook_retry_attempts,
                    )
                    sleep(delay)
                    continue
                raise
            except RequestException as exc:
                if attempt < settings.casebook_retry_attempts:
                    delay = _retry_delay_seconds(attempt, None)
                    logger.warning(
                        'Ошибка запроса Casebook для params=%s: %s. Повтор через %.1f сек (попытка %s/%s).',
                        params,
                        exc,
                        delay,
                        attempt + 1,
                        settings.casebook_retry_attempts,
                    )
                    sleep(delay)
                    continue
                raise

        if response is None:
            raise RuntimeError('Пустой ответ от Casebook API.')
        payload = response.json()

        batch = payload.get('items') or []
        items.extend(batch)
        page_number += 1
        logger.info(
            'Загрузка Casebook: страница=%s, получено_в_странице=%s, всего_получено=%s.',
            page_number,
            len(batch),
            len(items),
        )

        next_offset = payload.get('next')
        if not next_offset:
            break
        offset = next_offset

    return items


def build_casebook_documents_url(case_id: str) -> str:
    base_url = settings.casebook_api_url.split('/tracking/', 1)[0].rstrip('/')
    return f'{base_url}/cases/{case_id}/documents'


def fetch_case_documents(case_id: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    page = 1
    offset = None

    while True:
        params: dict[str, Any] = {'size': settings.page_size}
        if offset is not None:
            params['offset'] = offset
        else:
            params['page'] = page

        response = None
        for attempt in range(settings.casebook_retry_attempts + 1):
            try:
                response = requests.get(
                    build_casebook_documents_url(case_id),
                    headers=_build_casebook_headers(),
                    params=params,
                    timeout=40,
                )
                response.raise_for_status()
                break
            except HTTPError as exc:
                status_code = response.status_code if response is not None else None
                if status_code == 404:
                    return documents
                is_retryable = status_code is not None and _is_retryable_casebook_status(status_code)
                if is_retryable and attempt < settings.casebook_retry_attempts:
                    retry_after = response.headers.get('Retry-After') if response is not None else None
                    delay = _retry_delay_seconds(attempt, retry_after)
                    logger.warning(
                        'Casebook %s для списка документов дела %s. Повтор через %.1f сек (попытка %s/%s).',
                        status_code,
                        case_id,
                        delay,
                        attempt + 1,
                        settings.casebook_retry_attempts,
                    )
                    sleep(delay)
                    continue
                raise
            except RequestException as exc:
                if attempt < settings.casebook_retry_attempts:
                    delay = _retry_delay_seconds(attempt, None)
                    logger.warning(
                        'Ошибка запроса списка документов дела %s: %s. Повтор через %.1f сек (попытка %s/%s).',
                        case_id,
                        exc,
                        delay,
                        attempt + 1,
                        settings.casebook_retry_attempts,
                    )
                    sleep(delay)
                    continue
                raise

        if response is None:
            raise RuntimeError(f'Пустой ответ от Casebook API для списка документов дела {case_id}.')

        payload = response.json()
        batch = payload.get('items') or []
        documents.extend(batch)

        next_offset = payload.get('next')
        if next_offset:
            offset = next_offset
            page += 1
            continue

        pages = payload.get('pages') or page
        if page >= pages:
            break
        page += 1

    return documents


def _case_document_to_payload_item(case_id: str, case_number: str, document: dict[str, Any]) -> dict[str, Any]:
    document_id = document.get('id')
    return {
        '_source': CASE_DOCUMENT_SNAPSHOT_SOURCE,
        'findDate': document.get('changeDate') or document.get('publishDate') or document.get('date'),
        'isDeleted': False,
        'eventType': 'added',
        'document': {
            'id': document_id,
            'actualDate': document.get('date') or document.get('incomingDate') or document.get('publishDate'),
            'type': document.get('type'),
            'decisionType': document.get('decisionType'),
            'contentTypes': document.get('contentTypes') or [],
            'isFinal': document.get('isFinalDocument'),
            'fileName': document.get('fileName'),
            'description': document.get('description'),
            'reasonDocumentId': document.get('reasonDocumentId'),
        },
        'eventData': {
            'case': {'id': case_id, 'number': case_number},
            'instance': {
                'id': document.get('instanceId'),
                'number': document.get('instanceNumber'),
                'level': document.get('instanceLevel'),
                'courtId': document.get('courtId'),
                'judgeId': (document.get('judgesIds') or [None])[0],
            },
            'discriminator': 'ArbitrageCaseDocumentSnapshot',
        },
    }


def fetch_case_document_snapshot_items(payload_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: dict[str, str] = {}
    for item in payload_items:
        case_obj = item.get('eventData', {}).get('case') or {}
        case_id = case_obj.get('id')
        case_number = case_obj.get('number')
        if case_id and case_number:
            cases[case_id] = case_number

    snapshot_items: list[dict[str, Any]] = []
    for case_id, case_number in cases.items():
        try:
            documents = fetch_case_documents(case_id)
        except Exception:
            logger.exception('Не удалось получить список документов дела %s.', case_id)
            continue

        for document in documents:
            if document.get('id'):
                snapshot_items.append(_case_document_to_payload_item(case_id, case_number, document))

    if snapshot_items:
        logger.info(
            'Получены документы карточек Casebook: дел=%s, документов=%s.',
            len(cases),
            len(snapshot_items),
        )

    return snapshot_items


def _sync_payload_items(payload_items: list[dict[str, Any]]) -> dict[str, int]:
    inserted = 0
    updated = 0
    skipped = 0
    processed = 0
    progress_every = max(1, settings.progress_log_every_items)
    seen_source_hashes: set[str] = set()

    with SessionLocal() as db:
        for item in payload_items:
            processed += 1
            source_hash = _build_hash(item)
            if source_hash in seen_source_hashes:
                skipped += 1
                continue
            seen_source_hashes.add(source_hash)

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

            document_id = document_obj.get('id')
            same_document_events = []
            if document_id:
                same_document_events = db.execute(
                    select(DocumentEvent).where(
                        DocumentEvent.case_id == case_db.id,
                        DocumentEvent.document_external_id == document_id,
                    )
                ).scalars().all()

            is_snapshot_item = item.get('_source') == CASE_DOCUMENT_SNAPSHOT_SOURCE
            if is_snapshot_item and any(
                (existing.raw_item or {}).get('_source') != CASE_DOCUMENT_SNAPSHOT_SOURCE
                for existing in same_document_events
            ):
                skipped += 1
                continue

            if not is_snapshot_item:
                for existing in same_document_events:
                    if (existing.raw_item or {}).get('_source') == CASE_DOCUMENT_SNAPSHOT_SOURCE:
                        db.delete(existing)
                if same_document_events:
                    db.flush()

            event_db = db.execute(
                select(DocumentEvent).where(DocumentEvent.source_hash == source_hash)
            ).scalar_one_or_none()
            if event_db:
                updated += 1
                event_db.raw_item = item
                event_db.event_type = item.get('eventType') or event_db.event_type
                event_db.find_date = _to_dt(item.get('findDate'))
                event_db.actual_date = _to_dt(document_obj.get('actualDate'))
                event_db.is_deleted = _to_bool(item.get('isDeleted'))
                # Refresh content types
                event_db.content_types.clear()
                db.flush()
            else:
                event_db = DocumentEvent(
                    case_id=case_db.id,
                    document_external_id=document_id,
                    event_type=item.get('eventType') or 'added',
                    find_date=_to_dt(item.get('findDate')),
                    actual_date=_to_dt(document_obj.get('actualDate')),
                    is_deleted=_to_bool(item.get('isDeleted')),
                    raw_item=item,
                    source_hash=source_hash,
                )
                db.add(event_db)
                db.flush()
                inserted += 1

            type_name = (document_obj.get('type') or {}).get('name', '').strip()
            seen_content_type_ids: set[str] = set()
            if content_types:
                for ct in content_types:
                    ct_id = ct.get('id')
                    ct_name = ct.get('name')
                    if not ct_id or not ct_name:
                        continue
                    if ct_id in seen_content_type_ids:
                        continue
                    seen_content_type_ids.add(ct_id)
                    composed_name = f'{type_name}: {ct_name}' if type_name else ct_name
                    db.add(
                        ContentType(
                            event_id=event_db.id,
                            content_type_external_id=ct_id,
                            name=composed_name,
                        )
                    )
            else:
                fallback_type_id = _compose_document_type_id(document_obj)
                fallback_type_name = _compose_document_type_name(document_obj)
                if fallback_type_id and fallback_type_name:
                    db.add(
                        ContentType(
                            event_id=event_db.id,
                            content_type_external_id=fallback_type_id,
                            name=fallback_type_name,
                        )
                    )

            if processed % progress_every == 0:
                logger.info(
                    'Прогресс обновления: обработано=%s/%s, добавлено=%s, обновлено=%s, пропущено=%s.',
                    processed,
                    len(payload_items),
                    inserted,
                    updated,
                    skipped,
                )

        db.commit()

    return {
        'fetched': len(payload_items),
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
    }


def sync_casebook_range(start_date: date | datetime, end_date: date | datetime) -> dict[str, int]:
    payload_items = fetch_casebook(start_date, end_date)
    payload_items.extend(fetch_case_document_snapshot_items(payload_items))
    return _sync_payload_items(payload_items)


def sync_casebook_all() -> dict[str, int]:
    payload_items = fetch_casebook()
    payload_items.extend(fetch_case_document_snapshot_items(payload_items))
    return _sync_payload_items(payload_items)


def sync_today_and_tomorrow() -> dict[str, int]:
    today_utc = datetime.now(timezone.utc).date()
    tomorrow_utc = today_utc + timedelta(days=1)
    return sync_casebook_range(today_utc, tomorrow_utc)


def sync_previous_six_hours() -> dict[str, int]:
    end_msk = datetime.now(CASEBOOK_DATE_TIMEZONE)
    start_msk = end_msk - timedelta(hours=6)
    logger.info(
        'Диапазон планового обновления Casebook за предыдущие 6 часов: dateFrom=%s, dateTo=%s.',
        _format_casebook_date_param(start_msk),
        _format_casebook_date_param(end_msk),
    )
    return sync_casebook_range(start_msk, end_msk)


def run_sync_with_logging(sync_kind: str, runner: Any) -> dict[str, int]:
    logger.info('Начало обновления данных (%s).', sync_kind)
    try:
        result = runner()
        logger.info(
            'Завершено обновление данных (%s). Получено: %s, добавлено: %s, обновлено: %s, пропущено: %s.',
            sync_kind,
            result['fetched'],
            result['inserted'],
            result['updated'],
            result['skipped'],
        )
        return result
    except Exception:  # pragma: no cover
        logger.exception('Ошибка обновления данных (%s).', sync_kind)
        raise
