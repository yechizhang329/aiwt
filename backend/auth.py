"""
JWT auth using existing users.yaml + bcrypt.
Issues HS256 tokens; Streamlit frontends exchange user credentials for JWT.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import yaml
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

import config

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token")

_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def _load_users() -> dict:
    with open(config.USERS_YAML) as f:
        data = yaml.safe_load(f)
    return data.get("credentials", {}).get("usernames", {})


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


def authenticate_user(username: str, password: str) -> Optional[dict]:
    users = _load_users()
    user = users.get(username)
    if not user:
        return None
    if not verify_password(password, user.get("password", "")):
        return None
    return {
        "username": username,
        "name": user.get("name", username),
        "email": user.get("email", ""),
        "role": user.get("role", "user"),
    }


def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=config.JWT_EXPIRE_MINUTES)
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except JWTError:
        raise _CREDENTIALS_EXCEPTION


async def get_current_user(token: str = Depends(_oauth2_scheme)) -> dict:
    payload = decode_token(token)
    username = payload.get("sub")
    if not username:
        raise _CREDENTIALS_EXCEPTION
    users = _load_users()
    user = users.get(username)
    if not user:
        raise _CREDENTIALS_EXCEPTION
    return {
        "username": username,
        "name": user.get("name", username),
        "email": user.get("email", ""),
        "role": user.get("role", "user"),
    }


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_admin_or_audit_agent(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") not in ("admin", "audit_agent"):
        raise HTTPException(status_code=403, detail="Admin or audit_agent role required")
    return user
