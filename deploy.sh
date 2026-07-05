#!/bin/bash
# 자동매매 배포 스크립트 — GCP 서버에서 수동 실행 또는 GitHub Webhook 호출
#
# 사용법:
#   bash ~/auto-trader/deploy.sh                          # 기본 브랜치 배포
#   DEPLOY_BRANCH=claude/strategy-v2 bash deploy.sh       # 특정 브랜치 배포
#
# 특징:
#   - fetch + reset --hard 방식이라 브랜치 분기/로컬 변경으로 실패하지 않음
#     (.env, logs/trades.json, logs/positions.json 등은 git 미추적이라 안전)
#   - venv 없으면 자동 생성
#   - 재시작 후 서비스가 실제로 살아있는지 확인, 죽었으면 실패 코드 반환
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BRANCH="${DEPLOY_BRANCH:-claude/auto-trader-handover-nesasr}"
SERVICE="auto-trader"
LOG="$REPO_DIR/logs/deploy.log"
mkdir -p "$REPO_DIR/logs"

log() { echo "$1" | tee -a "$LOG"; }

log ""
log "=== 배포 시작 $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
log "브랜치: $BRANCH"
log "경로:   $REPO_DIR"

cd "$REPO_DIR"

# 1. 코드 동기화 — pull 대신 fetch+reset (분기·로컬수정 문제 원천 차단)
log ""
log "[1/5] git 동기화 (origin/$BRANCH 기준으로 강제 일치)..."
git fetch origin "$BRANCH" 2>&1 | tee -a "$LOG"
git checkout -B "$BRANCH" "origin/$BRANCH" 2>&1 | tee -a "$LOG"
git reset --hard "origin/$BRANCH" 2>&1 | tee -a "$LOG"
log "  → 현재 커밋: $(git log --oneline -1)"

# 2. venv 확인/생성
log ""
log "[2/5] venv 확인..."
if [ ! -x "$REPO_DIR/venv/bin/pip" ]; then
  log "  → venv 없음, 새로 생성"
  python3 -m venv "$REPO_DIR/venv"
fi

# 3. 의존성 설치
log ""
log "[3/5] pip install..."
"$REPO_DIR/venv/bin/pip" install -r requirements.txt -q 2>&1 | tee -a "$LOG"
log "  → 완료"

# 4. 서비스 재시작
log ""
log "[4/5] 서비스 재시작..."
systemctl --user restart "$SERVICE" 2>&1 | tee -a "$LOG"

# 5. 기동 확인 — 5초 후에도 active면 성공
sleep 5
log ""
log "[5/5] 서비스 상태:"
if systemctl --user is-active --quiet "$SERVICE"; then
  systemctl --user status "$SERVICE" --no-pager -l 2>&1 | tail -5 | tee -a "$LOG"
  log ""
  log "=== ✅ 배포 성공 $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
else
  log "❌ 서비스가 기동 직후 종료됨 — 최근 로그:"
  journalctl --user -u "$SERVICE" --no-pager -n 30 2>&1 | tee -a "$LOG"
  log ""
  log "=== ❌ 배포 실패 $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  exit 1
fi
