from urllib.parse import urlencode

import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, func, select

from .auth import create_access_token, require_auth
from .config import settings
from .db import BitrixPortal, Case, ContentType, DocumentEvent, SessionLocal, init_db

app = FastAPI(title='Casebook Widget API')


EVENT_TRANSLATIONS = {
    'added': 'Добавлено',
    'edit': 'Изменено',
}


@app.on_event('startup')
def startup_event() -> None:
    init_db()


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.api_route('/bitrix/install', methods=['GET', 'POST'])
async def bitrix_install(request: Request):
    payload = dict(request.query_params)
    if request.method == 'POST':
        body = await request.json()
        payload.update(body)

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

    return {'status': 'installed'}


@app.api_route('/bitrix/uninstall', methods=['GET', 'POST'])
async def bitrix_uninstall(request: Request):
    payload = dict(request.query_params)
    if request.method == 'POST':
        body = await request.json()
        payload.update(body)

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
    payload = dict(request.query_params)
    if request.method == 'POST':
        body = await request.json()
        payload.update({k: str(v) for k, v in body.items()})

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


@app.get('/cases')
def list_cases(search: str | None = None, _: dict = Depends(require_auth)):
    with SessionLocal() as db:
        stmt = (
            select(Case.external_case_id, Case.case_number, func.max(DocumentEvent.find_date).label('latest'))
            .join(DocumentEvent, DocumentEvent.case_id == Case.id)
            .group_by(Case.external_case_id, Case.case_number)
            .order_by(Case.case_number.asc())
        )
        if search:
            stmt = stmt.where(Case.case_number.ilike(f'%{search}%'))

        rows = db.execute(stmt).all()

    return [
        {
            'caseId': row.external_case_id,
            'caseNumber': row.case_number,
            'caseLink': f'https://kad.arbitr.ru/Card/{row.external_case_id}',
            'latestFindDate': row.latest.isoformat() if row.latest else None,
        }
        for row in rows
    ]


@app.get('/cases/{case_external_id}/history')
def case_history(case_external_id: str, _: dict = Depends(require_auth)):
    with SessionLocal() as db:
        case = db.execute(select(Case).where(Case.external_case_id == case_external_id)).scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=404, detail='Case not found')

        stmt = (
            select(DocumentEvent, ContentType)
            .join(ContentType, ContentType.event_id == DocumentEvent.id)
            .where(DocumentEvent.case_id == case.id)
            .order_by(DocumentEvent.find_date.asc(), DocumentEvent.actual_date.asc())
        )
        rows = db.execute(stmt).all()

    return [
        {
            'findDate': event.find_date.isoformat() if event.find_date else None,
            'actualDate': event.actual_date.isoformat() if event.actual_date else None,
            'eventType': EVENT_TRANSLATIONS.get(event.event_type, event.event_type),
            'contentTypeName': content.name,
        }
        for event, content in rows
    ]


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
