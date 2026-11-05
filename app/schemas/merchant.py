from typing import Literal

from pydantic import BaseModel, Field, field_validator

Step = Literal["store_name", "menus", "payment", "hours", "story"]


class SessionStartIn(BaseModel):
    store_id: int | None = Field(None, description="기존 가게를 다시 등록할 때만")
    market_id: int | None = Field(None, description="새 가게가 속할 시장. 비우면 첫 번째 시장")


class QuestionOut(BaseModel):
    session_id: int
    step: str
    question_ko: str | None
    example_ko: str | None
    audio_url: str | None = Field(None, description="question_ko 를 읽어 주는 mp3 주소. mock·실패 시 null (글자만 보여 주기)")


class AnswerOut(BaseModel):
    session_id: int
    step: Step
    transcript: str
    parsed: dict = Field(
        description="단계별 형태 — store_name: {name_ko, address} / menus: {menus: [{name_ko, price, spicy_level, is_ai_guess}]} / "
        "payment: {payment: [cash|card|foreign_card|onnuri]} / hours: {hours} / story: {story_ko}"
    )
    needs_check: list[str]
    confirm_text_ko: str
    confirm_audio_url: str | None = Field(None, description="confirm_text_ko 를 읽어 주는 mp3 주소. mock·실패 시 null")
    mode: str


class ConfirmIn(BaseModel):
    step: Step
    accepted: bool
    edits: dict | None = Field(None, description="사장님이 고친 값. parsed 와 같은 형태. 보내면 확정으로 처리")

    model_config = {"json_schema_extra": {"examples": [{"step": "store_name", "accepted": True}]}}


class ConfirmOut(QuestionOut):
    done: bool
    retry: bool = Field(description="true 면 같은 질문을 다시 한다 ('다시' 누름)")


class SummaryOut(BaseModel):
    session_id: int
    step: str
    done: bool
    store_id: int | None
    steps: dict
    needs_check: list[str]
    preview_en: dict | None = Field(description="외국인 화면 미리보기 {name, story, payment, hours, menus[]}")
    preview_zh: dict | None = Field(None, description="중국어 미리보기. preview_en 과 같은 모양 {translated, name, story, payment, hours, menus[]}")
    mode: str


class PublishOut(BaseModel):
    store_id: int
    status: str
    mode: str


class JobCreated(BaseModel):
    job_id: int
    status: str


class JobStatus(BaseModel):
    job_id: int
    status: Literal["queued", "processing", "done", "failed"]
    progress: int
    video_id: int | None
    error: str | None
    error_code: Literal["RECORDING_TOO_LONG", "VIDEO_FAILED"] | None = Field(
        description="failed 일 때만. RECORDING_TOO_LONG: 다시 녹음하거나 스크립트로 / VIDEO_FAILED: 다시 시도하거나 대체 영상"
    )
    fallback_video_id: int | None = Field(
        description="failed 일 때 대신 보여줄 영상(같은 가게의 기존 영상, 없으면 최신 시연 영상)"
    )


class StorePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    address: str | None = Field(None, max_length=300)
    payment: list[Literal["cash", "card", "foreign_card", "onnuri"]] | None = None
    hours: str | None = Field(None, max_length=200)
    story_ko: str | None = Field(None, max_length=1000)

    # 생략은 "안 바꿈", 명시적 null 은 거절. address 만 null 로 지울 수 있다.
    @field_validator("name", "payment", "hours", "story_ko")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("null 로 바꿀 수 없어요. 바꾸지 않으려면 필드를 빼 주세요.")
        return v


class MenuPatch(BaseModel):
    name_ko: str | None = Field(None, min_length=1, max_length=100)
    price: int | None = Field(None, ge=0, description="null 이면 가격 미정")
    spicy_level: int | None = Field(None, ge=0, le=3)

    @field_validator("name_ko", "spicy_level")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("null 로 바꿀 수 없어요. 바꾸지 않으려면 필드를 빼 주세요.")
        return v


# ---------- AI 인터뷰 (통화형) ----------


class InterviewStartIn(BaseModel):
    kind: Literal["register", "story"] = Field(description="register: 가게 정보 등록 / story: 숏폼용 사장님 이야기")
    store_id: int | None = Field(None, description="story 는 필수. register 는 기존 가게를 다시 등록할 때만")
    market_id: int | None = Field(None, description="register 새 가게가 속할 시장. 비우면 첫 번째 시장")


class InterviewTurnOut(BaseModel):
    interview_id: int
    kind: str
    turn: int = Field(description="다음 답을 보낼 때 같이 보낼 번호 (중복 전송 방지)")
    transcript: str | None = Field(description="방금 알아들은 사장님 말")
    heard: bool = Field(description="false 면 못 알아들어서 같은 질문을 다시 한다")
    say_ko: str = Field(description="AI 가 할 말 (맞장구 + 다음 질문, 끝이면 마무리 인사)")
    audio_url: str | None = Field(description="say_ko 음성. 받자마자 재생하고 끝나면 바로 듣기 시작. mock·실패 시 null")
    done: bool = Field(description="true 면 마무리 인사를 재생하고 더 듣지 않는다")
    progress: int = Field(description="0~100")
    draft: dict | None = Field(
        description="register 만. {store_name, category, items: [{name_ko, price, unit, spicy_level, is_ai_guess}], payment, hours, extras}"
    )
    covered: list[str] | None = Field(description="story 만. 이야기한 축 origin|years|craft|people|message")
    fillers: list[dict] | None = Field(
        None,
        description="시작할 때만. [{text, audio_url}] 짧은 맞장구. 지금은 쓰지 않아 빈 목록 (매번 들으면 거슬린다는 의견)",
    )
    mode: str


class InterviewOut(BaseModel):
    interview_id: int
    kind: str
    status: Literal["active", "done", "published"]
    store_id: int | None
    turns: list[dict] = Field(description="[{question, transcript}]")
    draft: dict | None
    needs_check: list[str]
    session_id: int | None = Field(
        description="register 가 끝나면 생기는 등록 세션. /onboarding/sessions/{id}/summary 로 외국인 화면 미리보기"
    )
    covered: list[str] | None
    quotes: list[dict] | None = Field(description="story 가 끝나면 영상에 쓸 원음 구간 [{turn, text}]. 고르는 중이면 null")
    script_ko: str | None
    mode: str


class DraftPatch(BaseModel):
    store_name: str | None = Field(None, min_length=1, max_length=100)
    category: str | None = Field(None, max_length=30)
    items: list[dict] | None = Field(None, description="[{name_ko, price, unit, spicy_level}] 전체 목록")
    payment: list[Literal["cash", "card", "foreign_card", "onnuri"]] | None = None
    hours: str | None = Field(None, max_length=200)
    extras: dict[str, str] | None = None
