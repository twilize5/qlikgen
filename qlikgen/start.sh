#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Kill background jobs on Ctrl-C
cleanup() {
  echo ""
  echo "==> Shutting down..."
  jobs -p | xargs -r kill 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo "==> Starting QIX sidecar on :8788"
(cd qix-node && node index.js) &

echo "==> Starting backend on :8787"
(cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload) &

echo "==> Starting UI on :5173"
(cd ui && npm run dev) &

sleep 2
# Open browser (best-effort, cross-platform)
if command -v xdg-open >/dev/null; then xdg-open http://localhost:5173 >/dev/null 2>&1 || true
elif command -v open >/dev/null; then open http://localhost:5173 || true
fi

echo ""
echo "==> QlikGen running. Ctrl-C to stop."
wait
