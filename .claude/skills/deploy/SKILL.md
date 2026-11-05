---
name: deploy
description: 현재 커밋을 BlueRack 서버에 배포하고 동작을 확인한다. "배포", "서버에 올려", "deploy" 요청에 사용.
---

# 배포

1. `git status --short`를 확인한다. 배포에 포함할 변경이 커밋되지 않았으면 커밋한다. 사용자가 직접 한 무관한 수정은 섞지 않고, 배포에서 빠진다고 알린다.
2. `.venv/bin/pytest`를 실행한다. 실패하면 배포하지 않고 원인을 보고한다.
3. `git push`로 GitHub에 올린다.
4. `bash deploy/deploy.sh`를 실행한다. 커밋된 HEAD만 배포되고, 실패하면 기존 코드가 남는다.
5. 확인:
   ```bash
   curl -fsS https://hack.220-66-233-75.nip.io/health
   curl -fsS -X POST https://hack.220-66-233-75.nip.io/ask -H 'Content-Type: application/json' -d '{"question":"연결 확인"}'
   ```
   `/ask` 응답의 `mode`를 보고한다. `mock`이면 서버에 키가 없다는 뜻이다. 실제 호출은 소액이지만 비용이 든다.
6. 실패하면 `ssh bluerack 'sudo journalctl -u hackathon -n 50 --no-pager'`로 로그를 본다. 로그에 키가 보여도 출력에 옮기지 않는다.

이 앱 영역(`/opt/hackathon`, `/etc/hackathon`, `hackathon` 서비스, Caddy `hack.` 블록) 밖은 건드리지 않는다.
