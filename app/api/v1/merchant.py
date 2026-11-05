"""사장님 API: 음성 대화형 등록, 숏폼 생성, 가게·메뉴 수정(P1)."""

import asyncio
import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ERROR_RESPONSES, AppError, not_found
from app.db.session import get_db
from app.llm import LLMProvider, get_llm
from app.models import Menu, Store, Video
from app.schemas.merchant import (
    AnswerOut,
    ConfirmIn,
    ConfirmOut,
    JobCreated,
    JobStatus,
    MenuPatch,
    PublishOut,
    QuestionOut,
    SessionStartIn,
    StorePatch,
    SummaryOut,
)
from app.services import onboarding, video
from app.services.ai import audio_ext
from app.services.i18n import LANGS

router = APIRouter(prefix="/merchant", tags=["merchant"], responses=ERROR_RESPONSES)

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 10 * 1024 * 1024


async def _read_limited(f: UploadFile, limit: int, what: str) -> bytes:
    # limit+1 바이트까지만 읽어서 큰 파일을 메모리에 다 올리지 않는다.
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise AppError("UPLOAD_TOO_LARGE", f"{what}은(는) {limit // 1024 // 1024}MB 이하만 올릴 수 있어요.", 413)
    return data


# ---------- 음성 대화형 등록 ----------


@router.post("/onboarding/sessions", response_model=QuestionOut, summary="등록 세션 시작")
def start_session(body: SessionStartIn | None = None, db: Session = Depends(get_db)):
    body = body or SessionStartIn()
    s = onboarding.start(db, body.store_id, body.market_id)
    return {"session_id": s.id, **onboarding.question(s.step)}


@router.post("/onboarding/sessions/{session_id}/answers", response_model=AnswerOut, summary="답변 제출 (음성 또는 텍스트)")
async def submit_answer(
    session_id: int,
    audio: UploadFile | None = File(None, description="webm 등 녹음 파일 (10MB 이하)"),
    text: str | None = Form(None, max_length=2000, description="음성 대신 글로 답할 때"),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
):
    s = onboarding.get_session(db, session_id)
    data = await _read_limited(audio, MAX_AUDIO_BYTES, "녹음 파일") if audio else None
    if not data and not (text and text.strip()):
        raise AppError("VALIDATION_ERROR", "audio 또는 text 중 하나를 보내 주세요.", 422)
    return await onboarding.answer(db, llm, s, data, audio.filename if audio else "", text)


@router.post("/onboarding/sessions/{session_id}/confirm", response_model=ConfirmOut, summary="확인 (맞아요/다시)")
def confirm(session_id: int, body: ConfirmIn, db: Session = Depends(get_db)):
    s = onboarding.get_session(db, session_id)
    return onboarding.confirm(db, s, body.step, body.accepted, body.edits)


@router.get("/onboarding/sessions/{session_id}/summary", response_model=SummaryOut, summary="전체 요약")
async def summary(session_id: int, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    return await onboarding.summary(db, llm, onboarding.get_session(db, session_id))


@router.post("/onboarding/sessions/{session_id}/publish", response_model=PublishOut, summary="가게 올리기 (번역 생성)")
async def publish(session_id: int, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    return await onboarding.publish(db, llm, onboarding.get_session(db, session_id))


# ---------- 숏폼 생성 ----------


@router.post("/stores/{store_id}/videos", status_code=202, response_model=JobCreated, summary="영상 생성 요청")
async def create_video(
    store_id: int,
    request: Request,
    background: BackgroundTasks,
    photos: list[UploadFile] = File(None, description="사진 1~5장 (필드 이름 photos 또는 photos[])"),
    script: str | None = Form(None, max_length=1000, description="영상 자막 스크립트 (선택). 그대로 자막이 된다"),
    audio: UploadFile | None = File(None, description="스크립트 대신 녹음 (선택). 글자로 바꿔 자막에만 쓰고, 영상에 소리는 넣지 않는다"),
    menu_id: int | None = Form(None),
    langs: list[str] = Form(None, description="자막 언어. 기본 en, zh (langs 또는 langs[], 콤마 구분도 가능)"),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
):
    store = db.get(Store, store_id)
    if not store:
        raise not_found("가게")
    form = await request.form()
    photos = photos or [f for f in form.getlist("photos[]") if hasattr(f, "read")]
    if not 1 <= len(photos) <= 5:
        raise AppError("VALIDATION_ERROR", "사진은 1~5장 올려 주세요.", 422)
    if menu_id is not None and not any(m.id == menu_id for m in store.menus):
        raise not_found("메뉴")
    raw_langs = langs or form.getlist("langs[]") or ["en", "zh"]
    langs = [x for x in dict.fromkeys(l.strip() for v in raw_langs for l in str(v).split(",")) if x in LANGS and x != "ko"]

    v = Video(store_id=store_id, menu_id=menu_id or (store.menus[0].id if store.menus else None), job_status="queued")
    db.add(v)
    db.commit()
    wd = video.work_dir(v.id)
    try:
        for i, f in enumerate(photos):
            data = await _read_limited(f, MAX_PHOTO_BYTES, "사진")
            # 디코딩은 무거워서 이벤트 루프 밖에서 한다.
            await asyncio.to_thread(video.prepare_photo, data, wd / f"photo_{i}.jpg")
        if script and script.strip():
            (wd / "script.txt").write_text(script.strip(), encoding="utf-8")
        elif audio:  # 스크립트가 있으면 녹음은 쓰지 않는다
            data = await _read_limited(audio, MAX_AUDIO_BYTES, "녹음 파일")
            if data:
                (wd / f"audio.{audio_ext(data)}").write_bytes(data)
    except AppError:
        shutil.rmtree(wd, ignore_errors=True)
        db.delete(v)
        db.commit()
        raise
    background.add_task(video.run_job, v.id, langs or ["en", "zh"], llm)
    return {"job_id": v.id, "status": v.job_status}


@router.get("/videos/jobs/{job_id}", response_model=JobStatus, summary="작업 상태 (2초 간격 폴링)")
def job_status(job_id: int, db: Session = Depends(get_db)):
    v = db.get(Video, job_id)
    if not v:
        raise not_found("작업")
    return {
        "job_id": v.id,
        "status": v.job_status,
        "progress": v.progress,
        "video_id": v.id if v.job_status == "done" else None,
        "error": v.error,
        "fallback_video_id": _fallback_video(db, v) if v.job_status == "failed" else None,
    }


def _fallback_video(db: Session, failed: Video) -> int | None:
    done = select(Video.id).where(Video.job_status == "done").order_by(Video.id.desc()).limit(1)
    return db.scalar(done.where(Video.store_id == failed.store_id)) or db.scalar(done)


# ---------- P1: 수정 ----------


@router.patch("/stores/{store_id}", response_model=PublishOut, summary="가게 수정 (P1, 번역 재생성)")
async def patch_store(store_id: int, body: StorePatch, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    store = db.get(Store, store_id)
    if not store:
        raise not_found("가게")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(store, field, value)
    db.commit()
    await onboarding.retranslate(db, llm, store)
    return {"store_id": store.id, "status": store.status, "mode": llm.name}


@router.patch("/menus/{menu_id}", response_model=PublishOut, summary="메뉴 수정 (P1, 번역 재생성)")
async def patch_menu(menu_id: int, body: MenuPatch, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    menu = db.get(Menu, menu_id)
    if not menu:
        raise not_found("메뉴")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(menu, field, value)
    menu.is_ai_guess = False
    db.commit()
    store = db.get(Store, menu.store_id)
    db.refresh(store)
    await onboarding.retranslate(db, llm, store)
    return {"store_id": store.id, "status": store.status, "mode": llm.name}
