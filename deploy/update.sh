#!/usr/bin/env bash
# 서버에서 코드 갱신: bash /opt/hackathon/app/deploy/update.sh
set -euo pipefail
cd /opt/hackathon/app
sudo -u hackathon git pull
sudo -u hackathon /opt/hackathon/venv/bin/pip install -q -r requirements.txt
sudo systemctl restart hackathon
sleep 2
curl -fsS http://127.0.0.1:8000/health && echo
