from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings
from app.utils.logger import get_logger
from app.utils.exceptions import AuthenticationError

logger = get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)


def create_access_token(payload: dict[str, Any]) -> str:
    """Create a signed JWT access token."""
    data = payload.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    data["exp"] = expire
    data["iat"] = datetime.now(timezone.utc)
    token = jwt.encode(data, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    logger.info("JWT token created", extra={"sub": data.get("sub")})
    return token


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises AuthenticationError on failure."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError(f"Invalid token: {exc}") from exc


async def get_current_user_payload(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency that extracts and validates the JWT from the
    Authorization: Bearer <token> header.
    Raises HTTP 401 if missing or invalid.
    """
    credentials: HTTPAuthorizationCredentials | None = await _bearer(request)
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
        return payload
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def require_hr_admin(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency that requires the caller to be an HR admin.
    Raises HTTP 403 if the JWT claim `is_hr_admin` is not True.
    """
    payload = await get_current_user_payload(request)
    if not payload.get("is_hr_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="HR admin access required",
        )
    return payload