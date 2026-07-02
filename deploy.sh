#!/bin/bash
# 자동 배포 스크립트 — GitHub Webhook 또는 수동 실행
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BRANCH="${DEPLOY_BRANCH:-claude/auto-trader-handover-nesasr}"
LOG="$REPO_DIR/logs/deploy.log"
mkdir -p "$REPO_DIR/logs"

echo "=== 배포 시작 $(date '+%Y-%m-%d %H:%M:%S KST') ===" | tee -a "$LOG"
echo "브랜치: $BRANCH" | tee -a "$LOG"

cd "$REPO_DIR"

# git pull
echo "[1/4] git pull..." | tee -a "$LOG"
git pull origin "$BRANCH" 2>&1 | tee -a "$LOG"

# 패키지 설치
echo "[2/4] pip install..." | tee -a "$LOG"
"$REPO_DIR/venv/bin/pip" install -r requirements.txt -q 2>&1 | tee -a "$LOG"

# 서비스 재시작
echo "[3/4] 서비스 재시작..." | tee -a "$LOG"
systemctl --user restart auto-trader 2>&1 | tee -a "$LOG"

# 상태 확인
sleep 3
echo "[4/4] 서비스 상태:" | tee -a "$LOG"
systemctl --user status auto-trader --no-pager -l 2>&1 | tail -5 | tee -a "$LOG"

echo "=== 배포 완료 $(date '+%Y-%m-%d %H:%M:%S KST') ===" | tee -a "$LOG"
