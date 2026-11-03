import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.llm import ChatMessage, LLMError, LLMProvider, get_llm
from app.prompts import DEFAULT_SYSTEM_PROMPT
from app.schemas.chat import AskRequest, AskResponse, ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


def _to_messages(body: ChatRequest) -> list[ChatMessage]:
    return [ChatMessage(role=m.role, content=m.content) for m in body.messages]


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, llm: LLMProvider = Depends(get_llm)):
    try:
        answer = await llm.complete(
            [ChatMessage(role="user", content=body.question)], body.system_prompt or DEFAULT_SYSTEM_PROMPT
        )
    except LLMError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return AskResponse(answer=answer, mode=llm.name)


@router.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, llm: LLMProvider = Depends(get_llm)):
    try:
        content = await llm.complete(_to_messages(body), body.system or DEFAULT_SYSTEM_PROMPT)
    except LLMError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return ChatResponse(content=content, mode=llm.name)


@router.post("/api/chat/stream")
async def chat_stream(body: ChatRequest, llm: LLMProvider = Depends(get_llm)):
    """Server-Sent Events. 프론트는 fetch + ReadableStream 으로 `data:` 줄을 읽는다.

    순서: `data: {"text": ...}` 반복 → 실패 시 `event: error` → 마지막 `event: done` ({"mode": ...})
    """

    async def events():
        try:
            async for text in llm.stream(_to_messages(body), body.system or DEFAULT_SYSTEM_PROMPT):
                yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
        except LLMError as e:
            yield f"event: error\ndata: {json.dumps({'detail': str(e)}, ensure_ascii=False)}\n\n"
        yield f"event: done\ndata: {json.dumps({'mode': llm.name})}\n\n"

    # X-Accel-Buffering: 프록시가 응답을 모아두지 않고 바로 흘려보내게 한다.
    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
