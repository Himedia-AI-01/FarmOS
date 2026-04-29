"""JWT 토큰 생성/검증 및 비밀번호 Bcrypt 해싱."""

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

# JWT 시크릿은 반드시 환경변수로 명시 주입해야 한다.
# 과거 `settings.JWT_SECRET_KEY or secrets.token_urlsafe(32)` 패턴은 시크릿 누락을
# 무음 폴백으로 가리고, 멀티워커 환경에서 워커마다 다른 키가 생성돼 모든 토큰을
# 침묵 무효화시키는 위험이 있었다. 부트 시점에 명시적으로 실패하도록 강제한다.
# HS256 권장: 256-bit (32 bytes) 이상.
_JWT_SECRET = (settings.JWT_SECRET_KEY or "").strip()
if not _JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET_KEY가 설정되지 않았습니다. .env 또는 환경변수에 32바이트 이상의 "
        "랜덤 시크릿을 주입한 뒤 재시작하세요. (예: `openssl rand -base64 48`)"
    )
if len(_JWT_SECRET.encode("utf-8")) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY가 너무 짧습니다 (32 bytes 미만). HS256 보안 권장값 미달. "
        "더 긴 시크릿으로 교체하세요. (예: `openssl rand -base64 48`)"
    )

SECRET_KEY = _JWT_SECRET
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


def decode_refresh_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None
