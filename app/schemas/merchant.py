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
