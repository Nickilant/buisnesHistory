from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .config import settings

bearer = HTTPBearer(auto_error=False)


def create_access_token(payload: dict) -> str:
    to_encode = payload.copy()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_exp_minutes)
    to_encode.update({'exp': expires})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def require_auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    if settings.allow_local_dev_auth:
        return {'sub': 'local-dev-user', 'member_id': 'local-dev', 'domain': 'local.test'}

    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing token')
    try:
        return jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid token') from exc
