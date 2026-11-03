#!/usr/bin/env bash
# 로컬에서 실행: 커밋된 HEAD 를 BlueRack 서버로 보내고 재시작한다.
#   bash deploy/deploy.sh
# 필요 조건: ~/.ssh/config 에 Host bluerack 이 있어야 한다 (README 참고).
# DB(/opt/hackathon/data)와 환경변수(/etc/hackathon/hackathon.env)는 건드리지 않는다.
set -euo pipefail
HOST="${DEPLOY_HOST:-bluerack}"
cd "$(git rev-parse --show-toplevel)"

if [ -n "$(git status --porcelain)" ]; then
  echo "주의: 커밋 안 된 변경은 배포되지 않습니다 (HEAD $(git rev-parse --short HEAD) 만 배포)."
fi

git archive --format=tar HEAD | ssh "$HOST" 'set -e
  sudo -u hackathon rm -rf /opt/hackathon/app.new
  sudo -u hackathon mkdir /opt/hackathon/app.new
  sudo -u hackathon tar -x -C /opt/hackathon/app.new
  sudo -u hackathon sh -c "cd /opt/hackathon/app.new && /opt/hackathon/venv/bin/pip install -q --disable-pip-version-check -r requirements.txt"
  # 압축 해제·설치가 끝난 뒤에 바꿔 끼운다. 실패하면 기존 코드가 그대로 남는다.
  sudo -u hackathon rm -rf /opt/hackathon/app.old
  sudo -u hackathon mv /opt/hackathon/app /opt/hackathon/app.old
  sudo -u hackathon mv /opt/hackathon/app.new /opt/hackathon/app
  sudo cp /opt/hackathon/app/deploy/hackathon.service /etc/systemd/system/hackathon.service
  sudo systemctl daemon-reload
  sudo systemctl restart hackathon
  for i in 1 2 3 4 5 6 7 8 9 10; do curl -fsS http://127.0.0.1:8000/health && echo && exit 0; sleep 1; done
  echo "health check 실패. 로그: sudo journalctl -u hackathon -n 50" >&2
  exit 1'
echo "배포 완료: https://hack.220-66-233-75.nip.io/health"
