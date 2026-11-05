# AGENTS.md

AI 코딩 에이전트(Claude Code, Codex, Cursor 등)용 작업 안내. 사람용 설명은 `README.md`를 본다.

## 프로젝트

- 하루짜리 해커톤 백엔드. FastAPI + SQLite(SQLAlchemy 2.x) + OpenAI API.
- 서비스 주제는 당일에 정해진다. `/ask`, `/api/chat`, `/api/items`는 갈아끼우기 쉽게 둔 예시다.
- 배포 주소: https://hack.220-66-233-75.nip.io (BlueRack VM, Caddy → `127.0.0.1:8000`)

## 명령어

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload    # http://127.0.0.1:8000, /docs
.venv/bin/pytest                           # 네트워크·키 없이 동작 (기본으로 live 제외)
.venv/bin/pytest -m live                   # 실제 OpenAI 호출, 비용 발생. 요청받았을 때만 실행
bash deploy/deploy.sh                      # 커밋된 HEAD를 서버에 배포
```

## 코드 구조와 규칙

- 단순하게 유지한다. 해커톤용이라 불필요한 추상화, 레이어, 설정을 추가하지 않는다. 주석은 꼭 필요한 곳에만 쓴다.
- 기능 추가 순서: `app/models/` → `app/schemas/` → `app/api/routes/` → `app/main.py`에 `include_router` → `tests/`에 테스트 추가
- LLM 호출: 라우터에서 `llm: LLMProvider = Depends(get_llm)`로 받아 `await llm.complete(messages, system)` 또는 `llm.stream(...)`을 호출한다.
  - 프로바이더별 SDK를 라우터에서 직접 부르지 않는다.
  - 프로바이더는 `app/llm/`에 있다: `openai`(기본), `anthropic`, `mock`
  - `LLM_PROVIDER`로 고르고, 키가 없으면 mock으로 동작한다.
- 응답에는 `mode`(프로바이더 이름)를 넣는다. 프론트가 mock인지 구분하는 데 쓴다.
- 시스템 프롬프트 기본값은 `app/prompts.py`에 있다.
- DB는 시작할 때 `create_all`로만 만든다. 마이그레이션 도구는 없다. 스키마를 바꾸면 `data/app.db`를 지운다.
- 설정은 `app/core/config.py`의 `Settings`에 두고 `.env.example`도 함께 갱신한다. 비밀 값은 `SecretStr`로 다룬다.
- 테스트는 `tests/conftest.py`가 mock 모드와 임시 DB를 강제한다. 외부 API를 부르는 테스트는 `tests/live/`에 두고 `@pytest.mark.live`를 붙인다.

## 보안 (반드시 지킬 것)

- API 키를 코드, 커밋, 프론트, 로그, 에러 응답에 넣지 않는다. `.env`와 서버의 `/etc/hackathon/hackathon.env`에만 둔다.
- 프로바이더의 원본 에러 메시지를 사용자에게 그대로 돌려주지 않는다. 키 일부가 섞일 수 있다.
  - `LLMError(메시지, status_code)`로 바꿔 던진다.
  - 로그에는 에러 종류, 상태, 코드만 남긴다.
- `.env`, `data/`는 커밋하지 않는다.

## 서버 (BlueRack VM)

- 접속: `ssh bluerack`
- 이 앱이 쓰는 영역은 아래뿐이다.
  - `/opt/hackathon`, `/etc/hackathon`
  - 포트 8000, systemd `hackathon`
  - `/etc/caddy/Caddyfile`의 `hack.` 블록
- VM은 다른 서비스와 인프라(Caddy, 80·443 포트, 3.3GiB RAM)를 공유한다.
  - 위 영역 밖의 포트, Caddy 블록, DB, 컨테이너, 설정은 읽거나 바꾸지 않는다.
  - 다른 서비스에 대한 설명을 레포에 넣지 않는다.
- Caddy는 `sudo systemctl reload caddy`를 쓴다. `restart`는 금지한다.
- 환경변수를 바꾼 뒤에는 `sudo systemctl restart hackathon`을 실행한다.
- 메모리 제한은 `MemoryMax=600M`이고 워커는 1개다. 무거운 의존성(로컬 모델, 큰 ML 라이브러리)은 추가하지 않는다.
- 서버의 데이터는 백업하지 않는다.
