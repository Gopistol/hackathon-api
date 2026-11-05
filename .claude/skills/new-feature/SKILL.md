---
name: new-feature
description: 해커톤 서비스 기능(API 엔드포인트, DB 테이블, LLM 기능)을 이 레포 규칙대로 추가한다. "기능 추가", "엔드포인트 만들어", "API 추가" 요청에 사용.
---

# 기능 추가

요청한 기능을 아래 순서로 추가한다. 규칙은 `AGENTS.md`를 따른다. 단순하게 만들고 필요 없는 레이어는 만들지 않는다.

1. **DB가 필요하면** `app/models/<이름>.py`에 SQLAlchemy 모델을 만들고 `app/models/__init__.py`에 import한다. `app/models/item.py`를 참고한다.
2. **스키마**: `app/schemas/<이름>.py`에 요청/응답 Pydantic 모델을 만든다. 입력에는 길이 제한(`Field(min_length=..., max_length=...)`)을 건다.
3. **라우터**: `app/api/routes/<이름>.py`에 `APIRouter(prefix="/api/<이름>")`를 만들고 `app/main.py`에 `include_router`로 등록한다.
   - DB: `db: Session = Depends(get_db)`
   - LLM: `llm: LLMProvider = Depends(get_llm)` → `await llm.complete([ChatMessage("user", ...)], system)`
   - LLM 응답에는 `mode=llm.name`을 넣는다.
   - `LLMError`는 `HTTPException(e.status_code, str(e))`로 바꾼다. `app/api/routes/chat.py` 참고.
   - 기능 전용 시스템 프롬프트는 `app/prompts.py`에 상수로 둔다.
4. **테스트**: `tests/test_<이름>.py`에 정상 케이스 하나와 검증 실패(422) 하나를 만든다. mock 모드라 LLM 답은 `[mock mode]`로 시작한다.
5. `.venv/bin/pytest`로 전체 테스트를 통과시킨다.
6. 추가한 엔드포인트와 요청/응답 예시를 짧게 보고한다. 프론트 팀원에게 그대로 전달할 수 있게 쓴다. 배포는 요청받았을 때만 한다(`/deploy`).

스키마 컬럼을 바꿨다면 로컬은 `data/app.db`를 지우라고 알린다. 서버 DB도 같은 처리가 필요하면 배포 전에 사용자에게 확인한다.
