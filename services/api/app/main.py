from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode

import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from requests import RequestException
from sqlalchemy import and_, func, select

from .auth import create_access_token, require_auth
from .config import settings
from .db import BitrixPortal, Case, ContentType, DocumentEvent, DocumentStatus, SessionLocal, init_db

app = FastAPI(title='Casebook Widget API')
cors_origins = [origin.strip() for origin in settings.cors_allow_origins.split(',') if origin.strip()]
allow_all_origins = '*' in cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'] if allow_all_origins else cors_origins,
    allow_credentials=not allow_all_origins,
    allow_methods=['*'],
    allow_headers=['*'],
)


EVENT_TRANSLATIONS = {
    'added': 'Добавлено',
    'changed': 'Изменено',
}

PROCESSING_FILTER_VALUES = {'all', 'processed', 'unprocessed'}


def normalize_processing_filter(value: str | None) -> str:
    normalized = (value or 'all').strip().lower()
    if normalized not in PROCESSING_FILTER_VALUES:
        raise HTTPException(status_code=400, detail='Invalid processed filter')
    return normalized


def apply_processing_filter(stmt, processed: str):
    if processed == 'processed':
        return stmt.where(DocumentStatus.is_processed.is_(True))
    if processed == 'unprocessed':
        return stmt.where(func.coalesce(DocumentStatus.is_processed, False).is_(False))
    return stmt


async def read_payload(request: Request) -> dict[str, str]:
    payload = dict(request.query_params)
    if request.method != 'POST':
        return payload

    content_type = (request.headers.get('content-type') or '').lower()
    if 'application/json' in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict):
                payload.update({k: str(v) for k, v in body.items()})
                return payload
        except Exception:
            pass

    try:
        form = await request.form()
        payload.update({k: str(v) for k, v in form.items()})
    except Exception:
        body = (await request.body()).decode('utf-8', errors='ignore')
        if body:
            payload.update({k: v[0] for k, v in parse_qs(body).items() if v})

    return payload


@app.on_event('startup')
def startup_event() -> None:
    init_db()


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.api_route('/bitrix/install', methods=['GET', 'POST'])
async def bitrix_install(request: Request):
    payload = await read_payload(request)

    domain = payload.get('DOMAIN') or payload.get('domain')
    member_id = payload.get('member_id') or payload.get('memberId')
    if not domain or not member_id:
        raise HTTPException(status_code=400, detail='DOMAIN and member_id are required')

    with SessionLocal() as db:
        portal = db.execute(select(BitrixPortal).where(BitrixPortal.domain == domain)).scalar_one_or_none()
        if not portal:
            portal = BitrixPortal(domain=domain, member_id=member_id)
            db.add(portal)

        portal.member_id = member_id
        portal.access_token = payload.get('AUTH_ID') or payload.get('access_token')
        portal.refresh_token = payload.get('REFRESH_ID') or payload.get('refresh_token')
        portal.expires_in = int(payload['expires_in']) if payload.get('expires_in') else None
        portal.is_active = True
        db.commit()

    should_redirect_to_frontend = any([
        payload.get('PLACEMENT'),
        payload.get('user_id'),
        payload.get('member_id') or payload.get('memberId'),
        payload.get('DOMAIN') or payload.get('domain'),
    ])

    if should_redirect_to_frontend:
        redirect_url = f"{settings.frontend_url}?{urlencode(payload)}" if payload else settings.frontend_url
        return RedirectResponse(url=redirect_url, status_code=307)

    return {'status': 'installed'}


@app.api_route('/bitrix/uninstall', methods=['GET', 'POST'])
async def bitrix_uninstall(request: Request):
    payload = await read_payload(request)

    domain = payload.get('DOMAIN') or payload.get('domain')
    if not domain:
        raise HTTPException(status_code=400, detail='DOMAIN is required')

    with SessionLocal() as db:
        portal = db.execute(select(BitrixPortal).where(BitrixPortal.domain == domain)).scalar_one_or_none()
        if portal:
            portal.is_active = False
            portal.access_token = None
            portal.refresh_token = None
            db.commit()

    return {'status': 'uninstalled'}


@app.api_route('/bitrix/widget', methods=['GET', 'POST'])
async def bitrix_widget(request: Request):
    payload = await read_payload(request)

    redirect_url = f"{settings.frontend_url}?{urlencode(payload)}" if payload else settings.frontend_url
    return RedirectResponse(url=redirect_url, status_code=307)


@app.post('/auth/bitrix-auto')
def bitrix_auto_login(payload: dict):
    member_id = payload.get('member_id')
    user_id = payload.get('user_id')
    domain = payload.get('domain')
    if not member_id or not user_id:
        raise HTTPException(status_code=400, detail='member_id and user_id are required')

    with SessionLocal() as db:
        if domain:
            portal = db.execute(
                select(BitrixPortal).where(and_(BitrixPortal.domain == domain, BitrixPortal.member_id == member_id))
            ).scalar_one_or_none()
        else:
            portal = db.execute(select(BitrixPortal).where(BitrixPortal.member_id == member_id)).scalar_one_or_none()

        if not portal or not portal.is_active:
            raise HTTPException(status_code=401, detail='Unknown or inactive portal')

        token = create_access_token({'sub': str(user_id), 'member_id': member_id, 'domain': portal.domain})
        return {'access_token': token, 'token_type': 'bearer'}


@app.post('/auth/local')
def local_login():
    if not settings.allow_local_dev_auth:
        raise HTTPException(status_code=403, detail='Local auth is disabled')

    token = create_access_token({'sub': 'local-dev-user', 'member_id': 'local-dev', 'domain': 'local.test'})
    return {'access_token': token, 'token_type': 'bearer'}


@app.post('/bitrix/token/refresh')
def refresh_bitrix_token(payload: dict, _: dict = Depends(require_auth)):
    domain = payload.get('domain')
    if not domain:
        raise HTTPException(status_code=400, detail='domain is required')

    with SessionLocal() as db:
        portal = db.execute(select(BitrixPortal).where(BitrixPortal.domain == domain)).scalar_one_or_none()
        if not portal or not portal.refresh_token:
            raise HTTPException(status_code=404, detail='Refresh token is missing')

        response = requests.get(
            f'https://{portal.domain}/oauth/token/',
            params={'grant_type': 'refresh_token', 'refresh_token': portal.refresh_token},
            timeout=20,
        )
        response.raise_for_status()
        token_data = response.json()

        portal.access_token = token_data.get('access_token')
        portal.refresh_token = token_data.get('refresh_token')
        portal.expires_in = token_data.get('expires_in')
        db.commit()

    return {'status': 'ok'}


@app.post('/admin/sync/full')
def run_full_sync(_: dict = Depends(require_auth)):
    if not settings.full_sync_secret:
        raise HTTPException(status_code=404, detail='Not found')

    target_url = f'{settings.updater_service_url.rstrip("/")}/sync/full'
    try:
        response = requests.post(
            target_url,
            headers={'X-Full-Sync-Secret': settings.full_sync_secret},
            timeout=180,
        )
    except RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                'Не удалось подключиться к updater service. '
                f'Проверьте UPDATER_SERVICE_URL (сейчас: {settings.updater_service_url}).'
            ),
        ) from exc

    if not response.ok:
        raise HTTPException(status_code=502, detail='Не удалось запустить полную синхронизацию')
    return response.json()


@app.get('/cases')
def list_cases(
    search: str | None = None,
    case_number: str | None = None,
    page: int = 1,
    page_size: int = 10,
    _: dict = Depends(require_auth),
):
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    with SessionLocal() as db:
        base_stmt = (
            select(Case.external_case_id, Case.case_number, func.max(DocumentEvent.find_date).label('latest'))
            .join(DocumentEvent, DocumentEvent.case_id == Case.id)
            .group_by(Case.external_case_id, Case.case_number)
        )
        if case_number:
            base_stmt = base_stmt.where(Case.case_number == case_number)
        elif search:
            base_stmt = base_stmt.where(Case.case_number.ilike(f'%{search}%'))

        total = db.execute(select(func.count()).select_from(base_stmt.order_by(None).subquery())).scalar() or 0
        rows = db.execute(base_stmt.order_by(Case.case_number.asc()).offset(offset).limit(page_size)).all()

    return {
        'items': [
            {
                'caseId': row.external_case_id,
                'caseNumber': row.case_number,
                'caseLink': f'https://kad.arbitr.ru/Card/{row.external_case_id}',
                'latestFindDate': row.latest.isoformat() if row.latest else None,
            }
            for row in rows
        ],
        'pagination': {
            'page': page,
            'pageSize': page_size,
            'total': total,
            'totalPages': max(1, (total + page_size - 1) // page_size),
        },
    }




@app.get('/events/history')
def events_history(
    search: str | None = None,
    case_number: str | None = None,
    document: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    processed: str | None = None,
    page: int = 1,
    page_size: int = 10,
    _: dict = Depends(require_auth),
):
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size
    processed_filter = normalize_processing_filter(processed)

    with SessionLocal() as db:
        stmt = (
            select(Case.external_case_id, Case.case_number, DocumentEvent, ContentType, DocumentStatus.is_processed)
            .join(DocumentEvent, DocumentEvent.case_id == Case.id)
            .join(ContentType, ContentType.event_id == DocumentEvent.id)
            .outerjoin(
                DocumentStatus,
                and_(
                    DocumentStatus.case_id == Case.id,
                    DocumentStatus.document_key == DocumentEvent.document_external_id,
                    DocumentStatus.content_type_external_id == ContentType.content_type_external_id,
                ),
            )
        )
        case_search_value = case_number or search
        if case_search_value:
            stmt = stmt.where(Case.case_number.ilike(f'%{case_search_value}%'))

        if document:
            stmt = stmt.where(ContentType.name.ilike(f'%{document}%'))

        stmt = apply_processing_filter(stmt, processed_filter)

        if date_from:
            try:
                from_dt = datetime.fromisoformat(date_from)
                if from_dt.tzinfo is None:
                    from_dt = from_dt.replace(tzinfo=timezone.utc)
                stmt = stmt.where(DocumentEvent.find_date >= from_dt)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail='Invalid date_from format') from exc

        if date_to:
            try:
                to_dt = datetime.fromisoformat(date_to)
                if to_dt.tzinfo is None:
                    to_dt = to_dt.replace(tzinfo=timezone.utc)
                stmt = stmt.where(DocumentEvent.find_date <= to_dt)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail='Invalid date_to format') from exc

        total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar() or 0
        rows = db.execute(
            stmt.order_by(
                DocumentEvent.find_date.desc().nulls_last(),
                DocumentEvent.actual_date.desc().nulls_last(),
            )
            .offset(offset)
            .limit(page_size)
        ).all()

    return {
        'items': [
            {
                'caseId': case_id,
                'caseNumber': case_number,
                'caseLink': f'https://kad.arbitr.ru/Card/{case_id}',
                'findDate': event.find_date.isoformat() if event.find_date else None,
                'actualDate': event.actual_date.isoformat() if event.actual_date else None,
                'eventType': EVENT_TRANSLATIONS.get(event.event_type, event.event_type),
                'contentTypeName': content.name,
                'eventDataId': (event.raw_item or {}).get('eventData', {}).get('id') or case_id,
                'documentId': event.document_external_id,
                'contentTypeId': content.content_type_external_id,
                'isProcessed': bool(is_processed),
            }
            for case_id, case_number, event, content, is_processed in rows
        ],
        'pagination': {
            'page': page,
            'pageSize': page_size,
            'total': total,
            'totalPages': max(1, (total + page_size - 1) // page_size),
        },
    }


@app.get('/cases/{case_external_id}/history')
def case_history(case_external_id: str, processed: str | None = None, _: dict = Depends(require_auth)):
    processed_filter = normalize_processing_filter(processed)

    with SessionLocal() as db:
        case = db.execute(select(Case).where(Case.external_case_id == case_external_id)).scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=404, detail='Case not found')

        stmt = (
            select(DocumentEvent, ContentType, DocumentStatus.is_processed)
            .join(ContentType, ContentType.event_id == DocumentEvent.id)
            .outerjoin(
                DocumentStatus,
                and_(
                    DocumentStatus.case_id == case.id,
                    DocumentStatus.document_key == DocumentEvent.document_external_id,
                    DocumentStatus.content_type_external_id == ContentType.content_type_external_id,
                ),
            )
            .where(DocumentEvent.case_id == case.id)
        )
        stmt = apply_processing_filter(stmt, processed_filter)
        rows = db.execute(
            stmt.order_by(
                DocumentEvent.actual_date.asc().nulls_last(),
                DocumentEvent.find_date.asc().nulls_last(),
            )
        ).all()

    return [
        {
            'findDate': event.find_date.isoformat() if event.find_date else None,
            'actualDate': event.actual_date.isoformat() if event.actual_date else None,
            'eventType': EVENT_TRANSLATIONS.get(event.event_type, event.event_type),
            'contentTypeName': content.name,
            'eventDataId': (event.raw_item or {}).get('eventData', {}).get('id') or case_external_id,
            'documentId': event.document_external_id,
            'contentTypeId': content.content_type_external_id,
            'isProcessed': bool(is_processed),
        }
        for event, content, is_processed in rows
    ]


@app.patch('/documents/status')
def update_document_status(payload: dict, _: dict = Depends(require_auth)):
    case_external_id = payload.get('caseId')
    document_key = payload.get('documentId') or payload.get('documentKey')
    content_type_external_id = payload.get('contentTypeId')
    is_processed = bool(payload.get('isProcessed'))

    if not case_external_id or not document_key or not content_type_external_id:
        raise HTTPException(status_code=400, detail='caseId, documentId and contentTypeId are required')

    with SessionLocal() as db:
        case = db.execute(select(Case).where(Case.external_case_id == case_external_id)).scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=404, detail='Case not found')

        status = db.execute(
            select(DocumentStatus).where(
                and_(
                    DocumentStatus.case_id == case.id,
                    DocumentStatus.document_key == document_key,
                    DocumentStatus.content_type_external_id == content_type_external_id,
                )
            )
        ).scalar_one_or_none()

        if not status:
            status = DocumentStatus(
                case_id=case.id,
                document_key=document_key,
                content_type_external_id=content_type_external_id,
            )
            db.add(status)

        status.is_processed = is_processed
        db.commit()

    return {
        'caseId': case_external_id,
        'documentId': document_key,
        'contentTypeId': content_type_external_id,
        'isProcessed': is_processed,
    }


@app.post('/bitrix/rest/{method:path}')
def bitrix_rest_proxy(method: str, payload: dict, claims: dict = Depends(require_auth)):
    domain = claims.get('domain')
    with SessionLocal() as db:
        portal = db.execute(select(BitrixPortal).where(BitrixPortal.domain == domain)).scalar_one_or_none()
        if not portal or not portal.access_token:
            raise HTTPException(status_code=404, detail='No access token for portal')

        url = f'https://{domain}/rest/{method}'
        response = requests.post(url, json={**payload, 'auth': portal.access_token}, timeout=20)
        response.raise_for_status()
        return response.json()
