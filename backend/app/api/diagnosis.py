import logging
import httpx
import os
import uuid
import io
import asyncio
from PIL import Image
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, asc

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser

from app.core.database import get_db
from app.models.diagnosis import DiagnosisHistory, DiagnosisChatMessage
from app.core.deps import get_current_user
from app.models.user import User
from app.core.config import settings

from app.services.diagnosis_agent import run_diagnosis
from app.services.pest_classifier import (
    classify_pest_image,
    ClassifierError,
    ConfigurationError as ClassifierConfigError,
)

from pydantic import BaseModel, Field

class CreateDiagnosisHistoryRequest(BaseModel):
    pest: str = Field(min_length=1, max_length=100)
    crop: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=100)
    image_url: str | None = None

class CreateChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)

router = APIRouter(prefix="/diagnosis", tags=["diagnosis"])
logger = logging.getLogger(__name__)

# 🔒 보안 설정: 업로드 크기 및 픽셀 제한
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB 상한
Image.MAX_IMAGE_PIXELS = 24_000_000   # 24MP 상한 (Decompression Bomb 방어)

# 💡 이미지 저장을 위한 디렉토리 경로 설정 (절대 경로 대응)
UPLOAD_DIR = Path(settings.UPLOAD_BASE_DIR) / "diagnosis"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@router.post("/upload")
async def upload_diagnosis_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """해충 이미지를 업로드하고 WebP로 변환 및 리사이징하여 저장."""
    try:
        # 1. 파일 크기 검증 (OOM 방어)
        contents = await file.read()
        if len(contents) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="파일 크기가 너무 큽니다. (최대 10MB)"
            )

        # 2. 이미지 읽기 및 변환 (블로킹 작업이므로 스레드에서 실행)
        try:
            image = await asyncio.to_thread(Image.open, io.BytesIO(contents))
            
            # 3. 이미지 리사이징 (최대 320x320, 비율 유지)
            # 💡 채팅방 말풍선 크기에 최적화된 사이즈로 조정
            await asyncio.to_thread(image.thumbnail, (320, 320))
            
            # 4. WebP 변환 및 저장
            file_id = str(uuid.uuid4())
            file_path = UPLOAD_DIR / f"{file_id}.webp"
            
            # RGB 모드로 변환
            if image.mode in ("RGBA", "P"):
                image = await asyncio.to_thread(image.convert, "RGB")
                
            await asyncio.to_thread(image.save, file_path, "WEBP", quality=85)

            # 5. 해충 분류 (RunPod 서버) — 실패해도 image_url 은 정상 반환 (graceful degrade)
            #    프론트엔드는 pest 가 없으면 testPest 로 폴백한다.
            image_url = f"/uploads/diagnosis/{file_id}.webp"
            response: dict = {"image_url": image_url}
            try:
                result = await classify_pest_image(
                    contents,
                    filename=file.filename or "image.jpg",
                    content_type=file.content_type or "image/jpeg",
                )
                response["pest"] = result.get("pred")
                response["pest_raw"] = result.get("raw")
                response["pest_elapsed_s"] = result.get("elapsed_s")
            except ClassifierConfigError:
                logger.info("PEST_CLASSIFIER_URL 미설정 — pest 자동분류 건너뜀")
            except ClassifierError:
                logger.exception("pest 자동분류 실패 (image_url=%s) — 폴백 진행", image_url)

            return response

        except Image.DecompressionBombError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="이미지 해상도가 너무 커서 처리할 수 없습니다."
            )
        except Exception as e:
            logger.warning(f"Invalid image upload attempt: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="유효하지 않은 이미지 파일입니다."
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Image upload and processing failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="이미지 처리 중 예상치 못한 오류가 발생했습니다."
        ) from e

@router.get("/history")
async def get_diagnosis_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """현재 로그인한 사용자의 모든 진단 기록 조회."""
    query = (
        select(DiagnosisHistory)
        .where(DiagnosisHistory.user_id == current_user.id)
        .order_by(DiagnosisHistory.created_at.desc())
    )
    result = await db.execute(query)
    histories = result.scalars().all()
    
    return [
        {
            "id": h.id,
            "pest": h.pest,
            "crop": h.crop,
            "region": h.region,
            "analysis_result": h.analysis_result,
            "image_url": h.image_url,
            "created_at": h.created_at.isoformat()
        } for h in histories
    ]

@router.post("/history")
async def create_diagnosis_history(
    payload: CreateDiagnosisHistoryRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """진단 결과 저장 (정상 상태 패스트트랙 포함)."""
    pest = payload.pest
    crop = payload.crop
    region = payload.region
    final_result: dict | None = None

    # 💡 정상 상태인 경우 AI 워크플로우를 타지 않고 즉시 반환
    if pest == "정상":
        final_result = {
            "result_text": (
                "🔍 분석 결과, 병해충이 감지되지 않은 **정상** 상태입니다.\n\n"
                "현재 작물에서 특별한 이상 징후가 발견되지 않았습니다. 매우 건강한 상태입니다! "
                "앞으로도 정기적인 예찰을 통해 현재의 건강한 상태를 잘 유지하시기 바랍니다.\n\n"
                "**🌿 관리 조언:**\n"
                "- 주기적인 예찰을 통해 해충의 초기 유입을 예방하세요.\n"
                "- 적절한 시비와 관수를 통해 작물의 기본 면역력을 높여주세요.\n"
                "- 농장 주변 잡초 정리 등 청결한 재배 환경을 유지하는 것이 중요합니다."
            )
        }
    else:
        try:
            async for node_name, state_data in run_diagnosis(pest, crop, region):
                if node_name == "generate_diagnosis":
                    analysis_result = state_data.get("analysis_result")
                    if isinstance(analysis_result, dict):
                        final_result = analysis_result
        except Exception:
            logger.exception("진단 워크플로우 실행 실패")
            raise HTTPException(status_code=500, detail="진단 결과 생성에 실패했습니다.")

    if not final_result or not final_result.get("result_text"):
        raise HTTPException(status_code=500, detail="진단 결과 생성에 실패했습니다.")

    try:
        new_history = DiagnosisHistory(
            user_id=current_user.id,
            pest=pest,
            crop=crop,
            region=region,
            analysis_result=final_result,
            image_url=payload.image_url
        )
        db.add(new_history)
        await db.commit()
        await db.refresh(new_history)

        # 💡 사용자가 이미지를 업로드한 경우, 채팅방 첫 번째 말풍선으로 이미지를 표시하기 위해
        # 'user' 역할의 메시지를 먼저 생성하여 저장합니다.
        if payload.image_url:
            user_img_msg = DiagnosisChatMessage(
                diagnosis_id=new_history.id,
                role="user",
                content=f"![업로드한 이미지]({payload.image_url})"
            )
            db.add(user_img_msg)

        parsed_text = final_result.get("result_text", "")
        if parsed_text:
            initial_msg = DiagnosisChatMessage(
                diagnosis_id=new_history.id,
                role="assistant",
                content=parsed_text
            )
            db.add(initial_msg)
            
        await db.commit()

        return {
            "type": "done",
            "data": {
                "id": new_history.id,
                "pest": new_history.pest,
                "crop": new_history.crop,
                "region": new_history.region,
                "analysis_result": new_history.analysis_result,
                "image_url": new_history.image_url,
                "created_at": new_history.created_at.isoformat()
            }
        }
    except Exception:
        logger.exception("Diagnosis history 저장 실패")
        raise HTTPException(status_code=500, detail="DB 저장 중 오류가 발생했습니다.")

@router.get("/history/{history_id}/chat")
async def get_chat_messages(
    history_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """특정 진단 기록에 연결된 모든 채팅 메시지 조회."""
    check_query = select(DiagnosisHistory).where(
        (DiagnosisHistory.id == history_id) & (DiagnosisHistory.user_id == current_user.id)
    )
    check_result = await db.execute(check_query)
    if not check_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="진단 기록을 찾을 수 없습니다.")

    query = (
        select(DiagnosisChatMessage)
        .where(DiagnosisChatMessage.diagnosis_id == history_id)
        .order_by(DiagnosisChatMessage.created_at.asc())
    )
    result = await db.execute(query)
    messages = result.scalars().all()
    
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat()
        } for m in messages
    ]

@router.post("/history/{history_id}/chat")
async def add_chat_message(
    history_id: int,
    payload: CreateChatMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """채팅 메시지 추가 (httpx 커넥션 누수 방지 적용)."""
    check_query = select(DiagnosisHistory).where(
        (DiagnosisHistory.id == history_id) & (DiagnosisHistory.user_id == current_user.id)
    )
    check_result = await db.execute(check_query)
    history = check_result.scalar_one_or_none()
    if not history:
        raise HTTPException(status_code=404, detail="진단 기록을 찾을 수 없습니다.")

    new_msg = DiagnosisChatMessage(
        diagnosis_id=history_id,
        role="user",
        content=payload.content
    )
    db.add(new_msg)
    await db.commit()
    await db.refresh(new_msg)
    
    if new_msg.role == "user":
        hist_query = select(DiagnosisChatMessage).where(
            DiagnosisChatMessage.diagnosis_id == history_id
        ).order_by(asc(DiagnosisChatMessage.created_at))
        hist_res = await db.execute(hist_query)
        db_msgs = hist_res.scalars().all()
        
        api_key = settings.LITELLM_API_KEY
        model_name = settings.LITELLM_MODEL

        ai_reply = "API 키가 없어 답변할 수 없습니다."
        if api_key:
            try:
                # async with 블록을 사용하여 커넥션 누수 방지
                async with httpx.AsyncClient(
                    http1=True,
                    http2=False,
                    timeout=httpx.Timeout(180.0, connect=20.0)
                ) as custom_async_client:
                    llm = ChatOpenAI(
                        model=model_name,
                        api_key=api_key,
                        base_url=settings.LITELLM_URL,
                        temperature=0.0,
                        http_async_client=custom_async_client,
                        extra_body={
                            "reasoning": {"effort": "minimal", "exclude": True}
                        }
                    )
                    
                    system_content = (
                        "당신은 친절하고 전문적인 'FarmOS 해충 진단봇'입니다.\n\n"
                        f"현재 상담 대상: {history.crop} ({history.pest})\n"
                        f"지역: {history.region}\n\n"
                        "[대화 스타일 가이드 - 반드시 준수]\n"
                        "1. 본론 중심: **질문에 답변할 때는 '저는 ~입니다'와 같은 자기소개나 불필요한 서론을 절대 하지 마세요.** 인사말도 생략하고 즉시 답변(본론)부터 정중하게 시작하세요.\n"
                        "2. 정체성(예외): 사용자가 '너는 누구야?', '무슨 모델이야?' 등 당신의 **정체성에 대해 직접적으로 물었을 때만** 자신을 'FarmOS 해충 진단봇'이라고 정중하게 소개하세요.\n"
                        "3. 정중한 어조: 모든 답변은 반드시 정중한 존댓말을 사용하며, 절대 반말을 하지 마세요.\n"
                        "4. 간결함: 핵심 정보 위주로 3~5문장 내외로 답변하고 마크다운을 활용하세요.\n"
                        "5. 맥락 유지: 이전 대화 내용을 기억하여 자연스럽게 상담을 이어가세요."
                    )
                    
                    chat_msgs = [SystemMessage(content=system_content)]
                    for m in db_msgs:
                        if m.role == "user":
                            chat_msgs.append(HumanMessage(content=m.content))
                        elif m.role == "assistant":
                            chat_msgs.append(AIMessage(content=m.content))
                            
                    chain = llm | StrOutputParser()
                    ai_reply = await chain.ainvoke(chat_msgs)
            except Exception:
                logger.exception("Chat generation error for history %s", history_id)
                ai_reply = "AI 답변 생성 중 오류가 발생했습니다."

        ai_msg = DiagnosisChatMessage(
            diagnosis_id=history_id,
            role="assistant",
            content=ai_reply
        )
        db.add(ai_msg)
        await db.commit()
        await db.refresh(ai_msg)
        
        return {
            "user_msg": {
                "id": new_msg.id,
                "role": new_msg.role,
                "content": new_msg.content,
                "created_at": new_msg.created_at.isoformat()
            },
            "ai_msg": {
                "id": ai_msg.id,
                "role": ai_msg.role,
                "content": ai_msg.content,
                "created_at": ai_msg.created_at.isoformat()
            }
        }
    
    return {"user_msg": {"id": new_msg.id, "role": new_msg.role, "content": new_msg.content}}

@router.delete("/history/{history_id}")
async def delete_diagnosis_history(
    history_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = delete(DiagnosisHistory).where(
        (DiagnosisHistory.id == history_id) & (DiagnosisHistory.user_id == current_user.id)
    )
    result = await db.execute(query)
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="기록을 찾을 수 없거나 삭제 권한이 없습니다.")
    await db.commit()
    return {"status": "success", "message": f"History {history_id} deleted."}
