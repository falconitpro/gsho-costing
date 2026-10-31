#!/usr/bin/env bash
# Pull-автодеплой (без секретов в GitHub): таймер systemd запускает раз в 2 минуты.
# Если в main новый коммит и все проверки GitHub Actions для него зелёные — обновляет стенд.
set -uo pipefail
cd /opt/gsho-costing
REPO=falconitpro/gsho-costing
git fetch -q origin main || exit 0
LOCAL=$(git rev-parse HEAD); REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0
# статус проверок коммита (публичный репозиторий — без токена)
RUNS=$(curl -fsS -m 20 -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$REPO/commits/$REMOTE/check-runs?per_page=50") || exit 0
STATE=$(printf '%s' "$RUNS" | python3 -c '
import json,sys
runs=[r for r in json.load(sys.stdin).get("check_runs",[]) if r["name"]!="deploy"]
if not runs: print("pending")
elif any(r["status"]!="completed" for r in runs): print("pending")
elif all(r["conclusion"] in ("success","skipped","neutral") for r in runs): print("success")
else: print("failure")')
case "$STATE" in
  success) echo "$(date -Is) deploy $REMOTE"; bash deploy/deploy-remote.sh ;;
  failure) [ -f /tmp/gsho-failed-$REMOTE ] || { echo "$(date -Is) skip $REMOTE: проверки CI не прошли"; touch /tmp/gsho-failed-$REMOTE; } ;;
  *) : ;;   # ждём окончания CI
esac
