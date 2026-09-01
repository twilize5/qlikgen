#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Backend: creating venv + installing Python deps"
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install --upgrade pip
pip install -r backend/requirements.txt
deactivate

echo "==> QIX sidecar: installing Node deps"
cd qix-node
npm install
cd "$ROOT"

echo "==> UI: installing Node deps"
cd ui
npm install
cd "$ROOT"

echo "==> Done. Run ./start.sh to launch."
