"""사장님 API: 음성 대화형 등록, 숏폼 생성, 가게·메뉴 수정(P1)."""

import asyncio
import json
import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Path, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ERROR_RESPONSES, AppError, not_found
from app.llm import LLMError
from app.db.session import get_db
from app.llm import LLMProvider, get_llm
from app.models import Menu, Store, Video
from app.schemas.merchant import (
    AnswerOut,
    ConfirmIn,
    ConfirmOut,
    DraftPatch,
    InterviewOut,
    InterviewStartIn,
    InterviewTurnOut,
    JobCreated,
    JobStatus,
    MenuPatch,
    PublishOut,
    QuestionOut,
    SessionStartIn,
    StorePatch,
    SummaryOut,
)
from app.services import interview, onboarding, tts, video
from app.services.ai import MAX_SCRIPT_LINES, audio_ext, split_script
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
def start_session(body: SessionStartIn | None = None, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    body = body or SessionStartIn()
    s = onboarding.start(db, body.store_id, body.market_id)
    q = onboarding.question(s.step)
    return {"session_id": s.id, **q, "audio_url": tts.audio_url(llm, q["question_ko"])}


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
    result = await onboarding.answer(db, llm, s, data, audio.filename if audio else "", text)
    return {**result, "confirm_audio_url": tts.audio_url(llm, result["confirm_text_ko"])}


@router.post("/onboarding/sessions/{session_id}/confirm", response_model=ConfirmOut, summary="확인 (맞아요/다시)")
def confirm(
    session_id: int,
    body: ConfirmIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
):
    s = onboarding.get_session(db, session_id)
    result = onboarding.confirm(db, s, body.step, body.accepted, body.edits)
    if result["done"]:
        background.add_task(onboarding.prefetch_translations, s.id, llm)
    return {**result, "audio_url": tts.audio_url(llm, result["question_ko"])}


@router.get(
    "/tts/{key}.mp3",
    response_class=FileResponse,
    summary="질문·확인 문장 음성 (audio_url 로 받은 주소). 처음엔 만드는 대로 스트리밍, 그 뒤엔 파일",
    responses={200: {"content": {"audio/mpeg": {}}}},
)
async def tts_audio(key: str = Path(pattern=r"^[0-9a-f]{20}$"), llm: LLMProvider = Depends(get_llm)):
    # 같은 주소는 항상 같은 음성이라 브라우저가 오래 저장해도 된다.
    cache = {"Cache-Control": "public, max-age=86400"}
    if path := tts.cached(key):
        return FileResponse(path, media_type="audio/mpeg", headers=cache)
    text = tts.registered_text(key)
    if text is None:
        raise not_found("음성")
    chunks = tts.speech_chunks(llm, key, text)
    try:
        first = await anext(chunks)  # 첫 조각을 받아야 실패를 에러 응답으로 돌려줄 수 있다
    except LLMError as e:
        raise AppError("TTS_FAILED", "음성을 만들지 못했어요. 글자로 보여 드릴게요.", 503) from e

    async def body():
        yield first
        async for chunk in chunks:
            yield chunk

    return StreamingResponse(body(), media_type="audio/mpeg", headers=cache)


@router.get("/onboarding/sessions/{session_id}/summary", response_model=SummaryOut, summary="전체 요약")
async def summary(session_id: int, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    return await onboarding.summary(db, llm, onboarding.get_session(db, session_id))


@router.post("/onboarding/sessions/{session_id}/publish", response_model=PublishOut, summary="가게 올리기 (번역 생성)")
async def publish(session_id: int, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    return await onboarding.publish(db, llm, onboarding.get_session(db, session_id))


# ---------- AI 인터뷰 (통화형) ----------
# 프론트: say_ko 음성 재생 → 끝나면 자동으로 녹음 → 말이 1~1.5초 멈추면 turns 로 보냄 → 반복. done 이면 멈춘다.


@router.post("/interviews", response_model=InterviewTurnOut, summary="AI 인터뷰 시작 (첫 질문)")
async def start_interview(body: InterviewStartIn, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    return interview.start(db, llm, body.kind, body.store_id, body.market_id)


@router.post("/interviews/{interview_id}/turns", response_model=InterviewTurnOut, summary="답 보내기 → 다음 질문")
async def interview_turn(
    interview_id: int,
    background: BackgroundTasks,
    audio: UploadFile | None = File(None, description="한 번의 답 녹음 (10MB 이하)"),
    text: str | None = Form(None, max_length=2000, description="음성 대신 글로 답할 때"),
    turn: int | None = Form(None, description="받은 turn 번호. 다르면 409 TURN_MISMATCH"),
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
):
    iv = interview.get(db, interview_id)
    data = await _read_limited(audio, MAX_AUDIO_BYTES, "녹음 파일") if audio else None
    if not data and not (text and text.strip()):
        raise AppError("VALIDATION_ERROR", "audio 또는 text 중 하나를 보내 주세요.", 422)
    out = await interview.turn(db, llm, iv, data, audio.filename if audio else "", text, turn)
    _after_done(background, iv, llm, out["done"])
    return out


@router.post("/interviews/{interview_id}/finish", response_model=InterviewTurnOut, summary="인터뷰 그만하기")
async def finish_interview(
    interview_id: int, background: BackgroundTasks, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)
):
    iv = interview.get(db, interview_id)
    out = interview.finish(db, llm, iv)
    _after_done(background, iv, llm, True)
    return out


def _after_done(background: BackgroundTasks, iv, llm: LLMProvider, done: bool) -> None:
    if not done:
        return
    if iv.kind == "register" and iv.status == "done":
        background.add_task(onboarding.prefetch_translations, iv.result["session_id"], llm)
    elif iv.kind == "story":
        background.add_task(interview.compose_in_background, iv.id, llm)


@router.get("/interviews/{interview_id}", response_model=InterviewOut, summary="인터뷰 결과")
def get_interview(interview_id: int, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    return interview.detail(interview.get(db, interview_id), llm)


@router.patch("/interviews/{interview_id}/draft", response_model=InterviewOut, summary="등록 내용 고치기 (register)")
def edit_interview_draft(
    interview_id: int,
    body: DraftPatch,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
):
    iv = interview.get(db, interview_id)
    interview.edit_draft(db, iv, body.model_dump(exclude_unset=True))
    background.add_task(onboarding.prefetch_translations, iv.result["session_id"], llm)
    return interview.detail(iv, llm)


@router.post("/interviews/{interview_id}/publish", response_model=PublishOut, summary="가게 올리기 (register)")
async def publish_interview(interview_id: int, db: Session = Depends(get_db), llm: LLMProvider = Depends(get_llm)):
    return await interview.publish(db, llm, interview.get(db, interview_id))


# ---------- 숏폼 생성 ----------


@router.post("/stores/{store_id}/videos", status_code=202, response_model=JobCreated, summary="영상 생성 요청")
async def create_video(
    store_id: int,
    request: Request,
    background: BackgroundTasks,
    photos: list[UploadFile] = File(None, description="사진 1~5장 (필드 이름 photos 또는 photos[])"),
    script: str | None = Form(None, max_length=1000, description="영상 자막 스크립트 (선택). 그대로 자막이 된다"),
    audio: UploadFile | None = File(None, description="스크립트 대신 녹음 (선택). 글자로 바꿔 자막에만 쓰고, 영상에 소리는 넣지 않는다"),
    interview_id: int | None = Form(
        None, description="끝난 이야기 인터뷰 (선택). 사장님 원음 구간을 이어 붙여 목소리·자막으로 쓴다. script·audio 보다 우선"
    ),
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
    if script and len(split_script(script, limit=None)) > MAX_SCRIPT_LINES:
        raise AppError(
            "SCRIPT_TOO_LONG", f"스크립트가 30초를 넘어요. 자막 {MAX_SCRIPT_LINES}줄(문장 {MAX_SCRIPT_LINES}개 안팎) 이하로 줄여 주세요.", 422
        )
    if menu_id is not None and not any(m.id == menu_id for m in store.menus):
        raise not_found("메뉴")
    quotes = None
    if interview_id is not None:
        iv = interview.get(db, interview_id)
        if iv.kind != "story" or iv.store_id != store_id:
            raise AppError("VALIDATION_ERROR", "이 가게의 이야기 인터뷰가 아니에요.", 422)
        if iv.status == "active":  # 통화 중에 영상 만들기를 눌렀으면 거기서 끝낸 것으로 본다
            interview.finish(db, llm, iv)
        await interview.compose(db, llm, iv)
        # 이야기가 거의 없으면 quotes 가 비고, 그때는 가게 기본 정보로 영상을 만든다
        quotes = interview.video_quotes(iv)
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
        if quotes:
            (wd / "interview.json").write_text(json.dumps({"quotes": quotes}, ensure_ascii=False), encoding="utf-8")
        elif script and script.strip():
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
        "error_code": v.error_code if v.job_status == "failed" else None,
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
