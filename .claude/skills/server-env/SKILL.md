---
name: server-env
description: 서버의 비밀이 아닌 환경변수(CORS_ORIGINS, OPENAI_MODEL, OPENAI_MAX_OUTPUT_TOKENS, LLM_PROVIDER 등)를 바꾸고 재시작한다. "CORS 추가", "모델 바꿔", "프론트 주소 등록" 요청에 사용.
---

# 서버 환경변수 변경

대상 파일은 `/etc/hackathon/hackathon.env`(root:hackathon 640)다.

**API 키(`*_API_KEY`)는 이 스킬로 다루지 않는다.** 키는 읽거나 출력하거나 쓰지 않는다. 사용자에게 아래를 직접 하라고 안내한다.
```bash
ssh bluerack
sudo nano /etc/hackathon/hackathon.env
sudo systemctl restart hackathon
```

비밀이 아닌 값은 다음 순서로 바꾼다.

1. 현재 값을 확인한다. 키 줄은 출력하지 않는다.
   ```bash
   ssh bluerack "sudo grep -v '_API_KEY' /etc/hackathon/hackathon.env"
   ```
2. 해당 줄만 바꾼다. 줄이 없으면 끝에 추가한다. `VAR`와 `VALUE`를 실제 값으로 바꿔 실행한다.
   ```bash
   ssh bluerack "sudo grep -q '^VAR=' /etc/hackathon/hackathon.env && sudo sed -i 's|^VAR=.*|VAR=VALUE|' /etc/hackathon/hackathon.env || echo 'VAR=VALUE' | sudo tee -a /etc/hackathon/hackathon.env >/dev/null"
   ```
   - `CORS_ORIGINS`는 쉼표로 구분한다. 기존 값을 지우지 말고 이어 붙인다. 끝의 `/`는 빼고 `https://` 같은 스킴은 포함한다.
   - 추론 모델(gpt-5, o-series)로 바꿀 때는 `OPENAI_MAX_OUTPUT_TOKENS`도 4000~8000으로 올린다.
3. 재시작하고 확인한다.
   ```bash
   ssh bluerack "sudo systemctl restart hackathon && sleep 2 && curl -fsS http://127.0.0.1:8000/health"
   ```
4. 기본값을 바꾼 거라면 로컬 `.env.example`과 `app/core/config.py`도 함께 고친다.
