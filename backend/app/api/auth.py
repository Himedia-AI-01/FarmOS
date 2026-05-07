import logging
import secrets
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import (
    create_access_token, create_refresh_token, decode_refresh_token,
    ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS,
)
from app.core import user_store
from app.models.user import User
from app.schemas.auth import (
    SignupRequest, LoginRequest, FindIdRequest, FindPasswordRequest,
    ResetPasswordRequest, UserResponse, OnboardingRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_KEY = "farmos_token"
REFRESH_COOKIE_KEY = "farmos_refresh_token"

# 비밀번호 재설정용 일회용 토큰 저장소 (인메모리, 5분 만료)
_reset_tokens: dict[str, dict] = {}  # {token: {"user_id": str, "expires": float}}
RESET_TOKEN_EXPIRE_SECONDS = 300  # 5분

# 로그인 브루트포스 방어 — IP별 시도 횟수 제한
_login_attempts: dict[str, list[float]] = defaultdict(list)
MAX_LOGIN_ATTEMPTS = 5    # 최대 시도 횟수
LOGIN_WINDOW_SECONDS = 60  # 시간 윈도우 (1분)


def _check_rate_limit(client_ip: str):
    """IP별 로그인 시도 횟수를 확인하고 초과 시 차단."""
    now = time.time()
    # 윈도우 밖의 오래된 기록 제거
    _login_attempts[client_ip] = [
        t for t in _login_attempts[client_ip]
        if now - t < LOGIN_WINDOW_SECONDS
    ]
    if len(_login_attempts[client_ip]) >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="로그인 시도가 너무 많습니다. 1분 후 다시 시도해주세요.",
        )
    _login_attempts[client_ip].append(now)


def _set_token_cookie(response: Response, token: str):
    response.set_cookie(
        key=COOKIE_KEY,
        value=token,
        httponly=True,
        secure=settings.COOKIE_SECURE,  # .env 의 COOKIE_SECURE 로 결정 — 프로덕션 = true
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        domain=settings.COOKIE_DOMAIN or None,
    )


def _set_refresh_cookie(response: Response, token: str):
    response.set_cookie(
        key=REFRESH_COOKIE_KEY,
        value=token,
        httponly=True,
        secure=settings.COOKIE_SECURE,  # .env 의 COOKIE_SECURE 로 결정 — 프로덕션 = true
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
        domain=settings.COOKIE_DOMAIN or None,
    )


REGIONS = [
    "서울", "인천", "대전", "대구", "광주", "부산", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"
]

def _user_response(user: User) -> UserResponse:
    """User 모델 → UserResponse 변환 헬퍼."""
    # 💡 주소 앞부분을 파싱하여 지역 카테고리 추출 (프론트엔드 편의용)
    location_category = ""
    if user.location:
        location_category = next((r for r in REGIONS if user.location.startswith(r)), "")

    return UserResponse(
        user_id=user.id, name=user.name, email=user.email,
        location=user.location, 
        location_category=location_category, # 💡 파싱된 값 추가
        area=user.area, farmname=user.farmname,
        profile=user.profile, status=user.status,
        onboarding_completed=user.onboarding_completed,
        main_crop=user.main_crop, crop_variety=user.crop_variety,
        farmland_type=user.farmland_type,
        is_promotion_area=user.is_promotion_area,
        has_farm_registration=user.has_farm_registration,
        farmer_type=user.farmer_type,
        years_rural_residence=user.years_rural_residence,
        years_farming=user.years_farming,
    )


@router.post("/signup", response_model=UserResponse, status_code=201)
async def signup(req: SignupRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """회원가입 — 계정 생성 후 자동 로그인(쿠키 설정)."""
    user = await user_store.create_user(
        db, user_id=req.user_id, name=req.name, email=req.email,
        password=req.password, location=req.location,
        area=req.area, farmname=req.farmname, profile=req.profile,
    )
    if not user:
        raise HTTPException(400, "이미 사용 중인 아이디 또는 이메일입니다.")
    token = create_access_token({"sub": user.id, "name": user.name})
    refresh_token = create_refresh_token({"sub": user.id, "name": user.name})
    _set_token_cookie(response, token)
    _set_refresh_cookie(response, refresh_token)
    return _user_response(user)


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """로그인 — HttpOnly 쿠키로 JWT 설정 (IP별 시도 횟수 제한)."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    user = await user_store.authenticate(db, req.user_id, req.password)
    if not user:
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")

    # 로그인 성공 시 해당 IP의 시도 기록 초기화
    _login_attempts.pop(client_ip, None)
    token = create_access_token({"sub": user.id, "name": user.name})
    refresh_token = create_refresh_token({"sub": user.id, "name": user.name})
    _set_token_cookie(response, token)
    _set_refresh_cookie(response, refresh_token)
    return {"user_id": user.id, "name": user.name}


@router.post("/logout")
async def logout(response: Response):
    """로그아웃 — 쿠키 삭제."""
    response.delete_cookie(key=COOKIE_KEY, path="/", domain=settings.COOKIE_DOMAIN or None)
    response.delete_cookie(key=REFRESH_COOKIE_KEY, path="/", domain=settings.COOKIE_DOMAIN or None)
    return {"message": "로그아웃되었습니다."}


@router.post("/refresh")
async def refresh_token(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Refresh Token으로 새 Access Token 발급."""
    refresh = request.cookies.get(REFRESH_COOKIE_KEY)
    if not refresh:
        raise HTTPException(401, "리프레시 토큰이 없습니다.")
    payload = decode_refresh_token(refresh)
    if payload is None:
        response.delete_cookie(key=REFRESH_COOKIE_KEY, path="/", domain=settings.COOKIE_DOMAIN or None)
        raise HTTPException(401, "리프레시 토큰이 만료되었거나 유효하지 않습니다.")
    user_id = payload.get("sub", "")
    user = await user_store.find_by_id(db, user_id)
    if user is None:
        raise HTTPException(401, "사용자를 찾을 수 없습니다.")
    new_access = create_access_token({"sub": user.id, "name": user.name})
    _set_token_cookie(response, new_access)
    return {"message": "토큰이 갱신되었습니다."}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """현재 로그인한 사용자 정보 반환 — 쿠키 토큰 검증."""
    return _user_response(current_user)


@router.put("/onboarding", response_model=UserResponse)
async def complete_onboarding(
    req: OnboardingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """온보딩 완료 — 농장 프로필 저장."""
    user = await user_store.update_onboarding(db, current_user, req.model_dump(exclude_unset=True))
    return _user_response(user)


@router.post("/find-id")
async def find_id(req: FindIdRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """아이디 찾기 — 이름 + 이메일로 조회.

    보안: 일치 여부와 무관하게 동일 형식의 200 응답을 반환해 (이름, 이메일) 페어
    존재 여부를 누설하지 않는다. 일치 시에만 마스킹된 user_id를 응답에 포함.
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    user = await user_store.find_by_name_and_email(db, req.name, req.email)
    if user is None:
        # 일관된 응답 — 미일치 케이스에도 200 + 빈 user_id_masked
        return {"user_id_masked": "", "message": "조회를 완료했습니다."}
    uid = user.id
    masked = uid[:2] + "*" * (len(uid) - 2) if len(uid) > 2 else uid
    return {"user_id_masked": masked, "message": "조회를 완료했습니다."}


@router.post("/find-password")
async def find_password(req: FindPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """비밀번호 찾기 — 아이디 + 이메일 확인.

    보안:
      - 응답은 (id, email) 일치 여부와 무관하게 동일한 200 메시지 반환 (계정 열거 방지).
      - 일치하면 일회용 reset_token 을 발급해 이메일로 발송 (이메일 미구축 환경에서는
        토큰을 응답에 절대 포함하지 않음 — 과거 디자인의 평문 토큰 노출 취약점 제거).
      - 동일 IP 의 요청 횟수를 제한해 무차별 대입 방지 (`/login`과 동일한 윈도우).
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    user = await user_store.find_by_id_and_email(db, req.user_id, req.email)

    # 만료된 토큰 정리 (정상 흐름이든 미일치 흐름이든 GC 기회)
    now = time.time()
    expired = [k for k, v in _reset_tokens.items() if v["expires"] < now]
    for k in expired:
        del _reset_tokens[k]

    # 일치하는 사용자가 있으면 토큰 생성 + 이메일 발송 (없으면 조용히 스킵).
    # 응답은 양쪽 모두 동일하게 200을 반환 — (id, email) 페어 존재 여부 누설 차단.
    if user is not None:
        reset_token = secrets.token_urlsafe(32)
        _reset_tokens[reset_token] = {
            "user_id": req.user_id,
            "expires": now + RESET_TOKEN_EXPIRE_SECONDS,
        }
        if settings.PASSWORD_RESET_EMAIL_ENABLED:
            # TODO: 이메일 인프라 구축 후 SMTP/SES 발송 로직 연결.
            # 현재는 발송 인프라 미구축 — 토큰만 로그에 남기고 이메일 발송은 NoOp.
            logger.info(
                "password_reset.token_issued user=%s expires_in=%ds (email send: TODO)",
                req.user_id, RESET_TOKEN_EXPIRE_SECONDS,
            )
        else:
            # 운영자가 reset 흐름이 활성화되었음을 알 수 있도록 정보성 로그만 남김.
            # 토큰 본문은 로그에 절대 포함하지 않음 (로그 파일 노출 시 PII 누출 방지).
            logger.info(
                "password_reset.requested user=%s (PASSWORD_RESET_EMAIL_ENABLED=False, 토큰 미발송)",
                req.user_id,
            )

    # 동일한 메시지로 200 반환 — 계정 열거 차단
    return {
        "message": "입력하신 정보가 등록되어 있다면 비밀번호 재설정 안내를 이메일로 발송했습니다. "
                   "메일이 도착하지 않으면 입력 정보를 다시 확인해주세요.",
    }


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """비밀번호 재설정 — 일회용 토큰 검증 후 변경."""
    token_data = _reset_tokens.get(req.reset_token)
    if not token_data or token_data["expires"] < time.time():
        _reset_tokens.pop(req.reset_token, None)
        raise HTTPException(400, "재설정 토큰이 유효하지 않거나 만료되었습니다.")

    if token_data["user_id"] != req.user_id:
        raise HTTPException(400, "잘못된 요청입니다.")

    ok = await user_store.reset_password(db, req.user_id, req.new_password)
    if not ok:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")

    # 토큰 소멸 (일회용)
    del _reset_tokens[req.reset_token]
    return {"message": "비밀번호가 성공적으로 변경되었습니다."}
