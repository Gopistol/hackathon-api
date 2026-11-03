# Hackathon API (FastAPI)

BlueRack VM 에서 동작하는 해커톤 백엔드. VM 의 인프라(Caddy, 80·443 포트, 메모리)를 다른 서비스와 공유한다.
`https://hack.220-66-233-75.nip.io` → Caddy → `127.0.0.1:8000`.

공식 샘플 레포([halla-ai/hackathon-sample-2026](https://github.com/halla-ai/hackathon-sample-2026))의
`/ask` 형태와 mock 모드를 따른다. LLM 은 OpenAI API 를 직접 쓴다. `OPENAI_API_KEY` 만 넣으면 실제 호출로 바뀐다.

## 구조

```
app/
├── main.py              # 앱 생성, CORS, 라우터 등록, 시작 시 테이블 생성, / 데모 페이지
├── core/config.py       # 환경변수 설정 (Settings)
├── prompts.py           # DEFAULT_SYSTEM_PROMPT (팀 주제에 맞게 수정)
├── db/session.py        # SQLAlchemy engine, get_db, init_db
├── models/              # DB 테이블 (item.py 는 예시)
├── schemas/             # 요청/응답 Pydantic 스키마
├── api/routes/          # health, items(CRUD 예시), files, chat(/ask, /api/chat)
├── services/storage.py  # MinIO(S3 호환) 업로드/다운로드
└── llm/                 # LLM 프로바이더 추상화
    ├── base.py          #   LLMProvider 인터페이스 (name, complete, stream)
    ├── openai_provider.py
    ├── anthropic_provider.py
    ├── mock_provider.py #   키 없이 개발용 (토큰 0)
    └── __init__.py      #   get_llm(): LLM_PROVIDER 로 선택, 키 없으면 mock
static/index.html        # 프론트 준비 전 API 확인·백업 시연용 페이지
tests/                   # pytest (네트워크 없이 돈다), tests/live/ 는 실제 API 호출
deploy/                  # systemd 서비스, Caddy 블록, 갱신 스크립트
demo/                    # 백업 스크린샷·녹화 (제출물)
```

## 로컬 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env           # 키 없이 두면 mock 모드
.venv/bin/uvicorn app.main:app --reload
```

- 데모 페이지: http://127.0.0.1:8000
- Swagger: http://127.0.0.1:8000/docs

## OpenAI API 키 설정

1. https://platform.openai.com/api-keys 에서 키를 만든다 (결제 수단·크레딧 필요).
2. `.env` 에 넣는다. `.env` 는 `.gitignore` 에 있어 커밋되지 않는다.
   ```bash
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-4.1-mini      # 선택, 기본값
   ```
3. 서버를 재시작한다. 응답의 `mode` 가 `mock` → `openai` 로 바뀌면 연결된 것이다.
4. 서버(VM)에서는 같은 키를 `/etc/hackathon/hackathon.env` 에 넣고 `sudo systemctl restart hackathon`.

키는 코드·커밋·프론트에 넣지 않는다. 설정에서는 `SecretStr` 로 다루고, 에러 응답과 로그에는
에러 종류와 상태 코드만 남긴다 (OpenAI 원본 에러 메시지에는 키 일부가 들어갈 수 있다).

| `LLM_PROVIDER` | 필요한 값 | 응답 `mode` |
|---|---|---|
| `openai` (기본) | `OPENAI_API_KEY` | `openai` |
| `anthropic` | `ANTHROPIC_API_KEY` | `anthropic` |
| `mock` | 없음 | `mock` (답이 `[mock mode]` 로 시작) |

키가 비어 있으면 경고 로그를 남기고 mock 으로 동작한다.

### 오류 응답

| 상황 | HTTP | 메시지 |
|---|---|---|
| 응답 시간 초과 (`OPENAI_TIMEOUT`) | 504 | AI 응답 시간이 초과되었습니다 |
| 키가 틀림·권한 없음 | 502 | 서버의 OpenAI API 키 설정이 올바르지 않습니다 |
| 호출 한도 초과 | 429 | AI 호출 한도를 초과했습니다 |
| 크레딧 부족 (`insufficient_quota`) | 503 | OpenAI 크레딧이 부족합니다 |
| 모델 이름 오류 등 | 502 | AI 요청이 거부되었습니다 |
| 네트워크 오류 | 502 | AI 서버에 연결할 수 없습니다 |

에러 응답 본문은 `{"detail": "..."}`. 스트리밍(`/api/chat/stream`)은 `event: error` 로 같은 메시지를 보낸다.

새 프로바이더는 `app/llm/` 에 `name`, `complete()`, `stream()` 을 구현한 클래스를 만들고
`get_llm()` 의 `match` 에 추가한다. 라우터 코드는 바꿀 필요 없다.

## 테스트

```bash
.venv/bin/pytest               # 네트워크·키 없이 도는 테스트 (OpenAI 호출은 가짜로 대체)
.venv/bin/pytest -m live       # 실제 OpenAI 호출 (OPENAI_API_KEY 필요, 소액 비용 발생)
```

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | `{"status":"ok"}` |
| POST | `/ask` | `{"question": "...", "system_prompt": null}` → `{"answer", "mode"}` (공식 샘플과 같은 형태) |
| POST | `/api/chat` | `{"messages":[{"role":"user","content":"..."}], "system": null}` → `{"content", "mode"}` |
| POST | `/api/chat/stream` | 같은 요청, SSE: `data: {"text"}` 반복 → (실패 시 `event: error`) → `event: done` `{"mode"}` |
| GET/POST | `/api/items` | 예시 CRUD |
| GET/DELETE | `/api/items/{id}` | |
| POST | `/api/files` | multipart 업로드 (10MB 이하) |
| GET | `/api/files/{key}` | 다운로드 |

`system` 을 비우면 `app/prompts.py` 의 `DEFAULT_SYSTEM_PROMPT` 를 쓴다.

## 새 기능 추가 순서

1. `app/models/` 에 모델 추가 → `app/models/__init__.py` 에 import
2. `app/schemas/` 에 요청/응답 스키마
3. `app/api/routes/` 에 라우터 → `app/main.py` 에 `include_router`
4. LLM 이 필요하면 라우터에서 `llm: LLMProvider = Depends(get_llm)` 로 받아 `await llm.complete(...)`
5. `tests/` 에 테스트 한 개

테이블은 시작 시 `create_all` 로만 만든다. 컬럼을 바꾸면 SQLite 는 `data/app.db` 를 지우고 다시 띄운다.

## 프론트 연결 (해커톤 당일)

프론트 주소가 정해지면 `CORS_ORIGINS` 에 추가하고 재시작한다.
서버에서는 `/etc/hackathon/hackathon.env` 를 고친 뒤 `sudo systemctl restart hackathon`.

## 서버 배포 (BlueRack VM)

배포 완료 (2026-11-03): **https://hack.220-66-233-75.nip.io** (`/health`, `/docs`, `/` 데모 페이지)

| 항목 | 위치 |
|---|---|
| 코드 | `/opt/hackathon/app` (실행 계정 `hackathon`) |
| 가상환경 | `/opt/hackathon/venv` |
| SQLite DB | `/opt/hackathon/data/app.db` (배포해도 유지됨, 자동 백업 없음) |
| 환경변수 | `/etc/hackathon/hackathon.env` (root:hackathon 640) |
| 서비스 | `systemctl status hackathon`, 로그 `sudo journalctl -u hackathon -f` |
| Caddy | `/etc/caddy/Caddyfile` 끝의 `hack.` 블록, 접근 로그 `/var/log/caddy/hackathon.log` |

### SSH 접속 준비 (한 번만)

`~/.ssh/config` 에 추가하고 개인키를 `~/.ssh/` 에 둔다 (권한 600).

```
Host bluerack
    HostName access2.bluerack.org
    Port 31004
    User ubuntu
    IdentityFile ~/.ssh/<개인키 파일>
    IdentitiesOnly yes
```

### 코드 갱신

커밋한 뒤 로컬에서:

```bash
bash deploy/deploy.sh
```

커밋된 HEAD 를 서버로 보내 설치·재시작하고 health check 까지 한다. 실패하면 기존 코드가 남는다.
(레포가 비공개이고 서버에 Deploy key 를 등록하지 않았으므로 서버에서 `git pull` 하지 않는다.)

### 환경변수 변경 (API 키, CORS 등)

```bash
ssh bluerack
sudo nano /etc/hackathon/hackathon.env
sudo systemctl restart hackathon
```

### 주의

- 이 VM 은 다른 서비스와 공유한다. 다른 서비스가 쓰는 포트·Caddy 블록·DB·버킷·설정 파일은 건드리지 않는다.
- 서버 RAM 은 3.3GiB 를 공유한다. 워커 1개, `MemoryMax=600M`.
- Caddy 를 고칠 때 `sudo caddy validate` 가 새 로그 파일을 root 소유로 만들면 reload 가 실패한다.
  그때는 `sudo chown caddy:caddy /var/log/caddy/<파일>` 후 다시 `sudo systemctl reload caddy`.
- Caddy 는 `restart` 가 아니라 `reload` (같은 VM 의 다른 사이트가 끊기지 않음).

### 되돌리기

```bash
sudo systemctl disable --now hackathon
sudo cp /etc/caddy/Caddyfile.bak /etc/caddy/Caddyfile && sudo systemctl reload caddy
systemctl is-active caddy                              # Caddy 정상 확인
```

## 제출 전 체크리스트 (공식 가이드 기준)

- [ ] `pytest` 통과, `/health` 가 `{"status":"ok"}`
- [ ] `/ask` 가 범위 안 질문에는 답하고, 범위 밖 질문에는 정중히 거절 (`app/prompts.py`)
- [ ] `.env` 가 `git status` 에 안 보이고, 커밋 기록에도 키가 없음
- [ ] `demo/` 에 백업 스크린샷 또는 90초 녹화
- [ ] 아래 팀 섹션 작성: 문제·사용자·AI 흐름·데모 흐름, 리스크 5줄, 비용 메모
- [ ] 발표 화면에 키·`.env`·OpenAI 대시보드 탭이 보이지 않음
- [ ] 발표 10분 전: 반복 호출 스크립트 종료, 시연 URL 한 번 호출해 확인

## 팀 프로젝트 (해커톤 당일 작성)

- **문제 / 사용자**:
- **AI 흐름 (OpenAI 사용)**:
- **데모 흐름**:
- **리스크**: 프롬프트 인젝션 / 환각 / 개인정보 / 비용 / 대응 담당
- **비용 메모**: 사용한 모델(`OPENAI_MODEL`)과 대략적인 토큰 사용량
