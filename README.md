# 전통시장 AI 숏폼 API (FastAPI)

전통시장 사장님이 **말로 가게를 등록**하고 **사진 몇 장**을 올리면, AI가 **다국어 가게 정보**와
**20~30초 세로 숏폼(언어별 VTT 자막)**을 만들어 외국인 손님 피드에 올린다. 1차 대상: 서대문구 독립문영천시장·포방터시장.

- 배포: https://hack.220-66-233-75.nip.io (Swagger `/docs`)
- API 계약 기준: 노션 **04. 백엔드·API**. 경로·필드를 바꿀 때는 노션도 같이 고친다.

## 구조

```
app/
├── main.py              # 앱, CORS, 에러 핸들러, /api/v1 라우터, /media 정적 파일, 시작 시 시드
├── core/config.py       # 환경변수 (Settings)
├── core/errors.py       # 공통 에러 형식 {"error": {"code", "message"}}
├── api/v1/customer.py   # 시장·핀, 피드, 가게 상세, 주문 화면, 영상, K-콘텐츠
├── api/v1/merchant.py   # 음성 등록 세션, 영상 생성 job, 가게·메뉴 수정
├── api/v1/admin.py      # 데모 리셋
├── services/ai.py       # STT·단계별 정리·번역·자막 (mock/실패 시 대체 동작 포함)
├── services/onboarding.py, interview.py (통화형 AI 인터뷰), video.py (ffmpeg), maps.py (Google), i18n.py (언어·음식 용어집), views.py
├── models/, schemas/
├── seed.py, seed_data.py  # 멱등 시드·리셋, 시연용 가게 5개·K-콘텐츠 3개
├── prompts.py           # LLM 프롬프트
└── llm/                 # LLM 프로바이더 (openai 기본, anthropic, mock)
demo/                    # 미리 렌더링한 영상(media/), 원본 사진(photos/), markets.csv(있으면)
scripts/make_demo_media.py  # demo/photos 로 미리 렌더링 영상 다시 만들기
```

## 로컬 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env           # 키 없이 두면 mock 모드 (STT·LLM·번역 전부 고정 응답)
.venv/bin/uvicorn app.main:app --reload    # http://127.0.0.1:8000/docs
```

처음 시작할 때 DB가 비어 있으면 자동으로 시드한다. ffmpeg 는 PATH 에 없으면 `imageio-ffmpeg` 내장 바이너리를 쓴다.

## 시드와 리셋

| 방법 | 명령 |
|---|---|
| 비어 있을 때만 시드 | 앱 시작 시 자동, 또는 `.venv/bin/python -m app.seed` |
| 처음 상태로 되돌리기 (로컬) | `.venv/bin/python -m app.seed --reset` (서버 재시작 필요 없음) |
| 처음 상태로 되돌리기 (서버, 리허설용) | `curl -X POST https://hack.220-66-233-75.nip.io/api/v1/admin/reset -H "X-Admin-Token: $ADMIN_TOKEN"` |

리셋은 DB 테이블, 생성된 영상·자막, 사장님 음성을 지우고 시드를 다시 넣는다.

- **시장**: `demo/markets.csv`(전국전통시장표준데이터 CSV, [data.go.kr](https://www.data.go.kr/data/15012894/standard.do))가 있으면 서대문구만 넣는다. 없으면 영천·포방터 2곳을 대략 좌표로 넣는다.
- **발표용 영상 (우선)**: `demo/showcase/<가게 key>.mp4` 를 넣으면 임시 영상 대신 **이 영상만** 피드에 나온다.
  - 가게 key: `yeongcheon-oil`, `yeongcheon-donut`, `dongnimmun-bunsik`, `pobangteo-tteokbokki`, `hongeun-bindaetteok` (`app/seed_data.py`)
  - 선택: `<key>.jpg` 썸네일(없으면 1초 지점에서 자동 추출), `<key>.ko.vtt`·`<key>.en.vtt`·`<key>.zh.vtt` 자막(없으면 자막 없음, 자막을 영상에 구웠다면 안 넣어도 됨)
  - 피드 순서는 위 key 순서. 파일당 100MB 미만(GitHub 제한), 무대 재생을 생각하면 20MB 이하 권장
  - 넣은 뒤: 커밋 → `bash deploy/deploy.sh` → 리셋
- **실시간 생성 영상**: `FEED_DEMO_ONLY=true`(기본)면 피드에 올라가지 않는다. 진행률·`/videos/{id}`·가게 상세에서만 보인다.
- **K-콘텐츠 카드 이미지**: `demo/contents/<key>.jpg` (`scripts/make_content_cards.py` 로 만든 더미 카드). 포스터·스틸컷은 저작권 때문에 쓰지 않는다. 사용 허가된 이미지가 생기면 같은 파일 이름으로 바꿔 넣고 배포·리셋
- **임시 영상**: 발표용 영상이 없을 때 `demo/media/` 의 미리 렌더링한 영상 5개가 피드에 나온다. 실제 사진은
  `demo/photos/<가게 key>/` 에 3~5장 넣고 `.venv/bin/python scripts/make_demo_media.py` → 커밋 → 배포 → 리셋.

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` / `anthropic` / `mock`. 키가 비어 있으면 mock |
| `OPENAI_API_KEY` | | STT·정리·번역·자막 전부 이 키 하나 |
| `OPENAI_MODEL` | `gpt-4.1-mini` | |
| `OPENAI_STT_MODEL` | `gpt-4o-mini-transcribe` | |
| `OPENAI_MAX_OUTPUT_TOKENS` / `OPENAI_TIMEOUT` / `OPENAI_MAX_RETRIES` | `2048` / `15` / `0` | 데모용으로 짧게. 실패 시 단계별 대체 동작 |
| `CORS_ORIGINS` | localhost:3000,5173 | 프론트 주소 (콤마 구분) |
| `CORS_ORIGIN_REGEX` | | 프론트 주소 정규식. 서버: `^https://[a-z0-9-]+\.(vercel\.app\|netlify\.app)$` |
| `DATABASE_URL` | `sqlite:///./data/app.db` | 서버: `sqlite:////opt/hackathon/data/app.db` |
| `MEDIA_DIR` / `AUDIO_DIR` | `./data/media` / `./data/audio` | 서버: `/opt/hackathon/data/media`, `/opt/hackathon/data/audio` (**절대경로 필수**. 상대경로면 배포 때 지워짐) |
| `ADMIN_TOKEN` | | 리셋 엔드포인트 토큰. 비우면 리셋 꺼짐 |
| `GOOGLE_MAPS_API_KEY` | | (선택) 서버 IP 로 제한한 Geocoding 키. 비우면 가게 좌표 = 시장 좌표 근처 |
| `FEED_DEMO_ONLY` | `true` | 피드에 시연 영상만. 실시간 생성 영상은 피드 제외 |
| `FFMPEG_PATH` | | 비우면 PATH → imageio-ffmpeg |

## API 요약 (`/api/v1`, 언어 `lang=ko|en|zh`, 기본 en)

| 메서드 | 경로 | |
|---|---|---|
| GET | `/markets`, `/markets/{id}/stores` | 시장 목록, 지도 핀 (`directions_url` = Google Maps 길찾기) |
| GET | `/feed?cursor=` | 숏폼 피드 (10개씩, `next_cursor`) |
| GET | `/stores/{id}` | 가게 상세 (다국어 메뉴·먹는 법·맵기) |
| POST | `/orders/preview` | 주문 화면 (한국어 문장) |
| GET | `/videos/{id}` | 영상 상세 |
| POST | `/merchant/onboarding/sessions` | 등록 세션 시작 → 첫 질문 |
| POST | `/merchant/onboarding/sessions/{id}/answers` | multipart `audio` 또는 `text` |
| POST | `/merchant/onboarding/sessions/{id}/confirm` | `{step, accepted, edits?}` |
| GET | `/merchant/onboarding/sessions/{id}/summary` | 단계별 값, `needs_check`, `preview_en` |
| POST | `/merchant/onboarding/sessions/{id}/publish` | 저장 + 번역 → `{store_id}` |
| POST | `/merchant/interviews` | 통화형 AI 인터뷰 시작 `{kind: register\|story, store_id?, market_id?}` → 첫 질문 `say_ko`, `audio_url` |
| POST | `/merchant/interviews/{id}/turns` | multipart `audio` 또는 `text`, `turn` → 맞장구+다음 질문, `done`, `progress`, `draft`(register) |
| POST | `/merchant/interviews/{id}/finish` | 그만하기 |
| GET | `/merchant/interviews/{id}` | 결과: register `draft`·`needs_check`·`session_id` / story `quotes`·`script_ko` |
| PATCH | `/merchant/interviews/{id}/draft` | register 결과 고치기 |
| POST | `/merchant/interviews/{id}/publish` | register → `{store_id}` |
| POST | `/merchant/stores/{id}/videos` | multipart `photos`(1~5), `interview_id`(선택, 이야기 인터뷰 원음), `script`(선택), `audio`(선택, 스크립트 대신), `menu_id`, `langs` → 202 `{job_id}` |
| GET | `/merchant/videos/jobs/{job_id}` | `queued/processing/done/failed`, `progress` |
| PATCH | `/merchant/stores/{id}`, `/merchant/menus/{id}` | 수정 + 번역 재생성 (P1) |
| GET | `/contents`, `/contents/{id}/foods` | K-콘텐츠 (P1) |
| POST | `/admin/reset` | 데모 리셋 (`X-Admin-Token`) |

- 미디어 URL(`/media/...`)은 상대경로다. 프론트에서 API 주소를 앞에 붙인다.
- 에러: `{"error": {"code": "STT_FAILED", "message": "..."}}`. 코드: `VALIDATION_ERROR`, `NOT_FOUND`, `STT_FAILED`,
  `LLM_FAILED`, `UPLOAD_TOO_LARGE`, `INVALID_IMAGE`, `STEP_MISMATCH`, `NO_ANSWER`, `ONBOARDING_INCOMPLETE`, `ONBOARDING_DONE`, `FORBIDDEN`, `INTERNAL_ERROR`

### 통화형 AI 인터뷰

기존 등록(고정 질문 5개 + 맞아요/다시)은 그대로 두고 추가한 기능이다. 참고용 웹 페이지: `/demo/interview.html`

- **흐름 (버튼 없이)**: AI 음성 재생 → 끝나면 바로 녹음 → 말이 0.8초 멈추면 `turns` 로 전송하면서 맞장구(`fillers`, "네~")를 바로 재생 → 다음 AI 음성 … `done` 이면 멈춘다.
  말 끝 감지·녹음은 브라우저가 한다 (참고 페이지의 `listen()`). AI 가 말하는 동안에는 녹음하지 않는다(에코 방지).
- **한 턴**: 음성 인식 1번 + LLM 1번 (실측 1~3초). LLM 은 할 말과 바뀐 항목만 출력한다. `audio_url` 음성은 만드는 대로 스트리밍한다(처음 한 번, 그 뒤엔 저장한 파일).
- **register**: 가게 이름 → 업종 → 상품·가격(업종별 단위) → 결제 → 영업시간을 묻고, 업종별 질문(원산지, 맵기, 택배 등) 최대 2개 뒤 끝낸다.
  사장님이 말로 고치면 다음 턴에 반영. 끝나면 기존 등록 세션(done)이 생겨 `/onboarding/sessions/{session_id}/summary` 미리보기를 그대로 쓴다.
- **story**: 시작·세월·손맛·사람·한마디 축으로 꼬리 질문. 같은 축은 두 번까지만 묻는다. 시작 + 2개 축 + 구체적 일화가 나오면 끝낸다.
  끝나면 AI 가 답에서 **사장님 말 그대로인** 구간 2~5개(20~30초)를 고른다(원문에 없는 문장은 버림). 가게 `story_ko` 도 이 말로 바뀐다.
- **영상**: `interview_id` 를 주면 고른 구간을 원래 녹음에서 잘라 이어 붙여 목소리로 쓴다(ElevenLabs 음성 맞추기로 구간 시간 찾기, 실패하면 그 답 전체).
- 종료는 LLM 이 제안하고 서버 규칙(필수 항목, 최대 턴 register 12·story 8, "그만할게요")으로 확정한다.
- LLM 실패·mock: 고정 질문 + 규칙 기반 정리로 같은 흐름. 음성 인식 실패는 에러 대신 `heard: false` 로 같은 질문을 다시 한다.

### 영상 생성 규칙

- 자막 원문: 직접 쓴 `script` → 없으면 `audio` 를 STT 로 바꾼 글 → 둘 다 없으면 가게 기본 정보(이름·시장·대표 메뉴·가격·이야기)
- 사장님 문장은 고치지 않고 줄만 나눈다(줄당 25자 안팎). 6줄을 넘는 긴 스크립트만 AI 가 줄인다
- 길이 = 자막 줄 수 × 5초, 20~30초. 사진이 적으면 한 장을 확대·이동·축소 구간으로 나눠 보여준다
- 소리는 넣지 않는다 (녹음은 자막용으로만 쓴다)

### AI 실패 시 동작

| 단계 | 실패 시 |
|---|---|
| STT | `STT_FAILED` → 다시 말하기 또는 `text` 로 입력 |
| 단계별 정리 | JSON 파싱 1회 재시도 → 규칙 기반 정리 + `needs_check` 에 확인 요청 |
| 번역 | 음식 용어집 → 없으면 한국어 원문 |
| 영상 자막 | 사장님 스크립트 → 번역 실패 시 한국어 원문 / 스크립트 없으면 가게 정보로 만든 템플릿 문장 |
| 영상 렌더링 | job `failed`. 미리 렌더링한 영상은 피드에 남아 있다 |

API 키는 코드·커밋·응답·로그에 넣지 않는다. 프로바이더 원본 에러 메시지는 사용자에게 돌려주지 않는다.

## 테스트

```bash
.venv/bin/pytest               # 네트워크·키 없이 (mock 모드, 실제 ffmpeg 렌더링 포함)
.venv/bin/pytest -m live       # 실제 OpenAI 호출 (비용 발생)
```

## 서버 배포 (BlueRack VM)

배포 완료 (2026-11-03): **https://hack.220-66-233-75.nip.io** (`/health`, `/docs`)

| 항목 | 위치 |
|---|---|
| 코드 | `/opt/hackathon/app` (실행 계정 `hackathon`) |
| 가상환경 | `/opt/hackathon/venv` |
| SQLite DB | `/opt/hackathon/data/app.db` (배포해도 유지됨, 자동 백업 없음) |
| 미디어·음성 | `/opt/hackathon/data/media` (`/media` 로 공개), `/opt/hackathon/data/audio` (비공개) |
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
- [ ] 리허설 후 `POST /api/v1/admin/reset` 으로 데모 데이터 초기화
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
