#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

rm -f ./getchu.db
rm -rf ./.venv

mkdir -p ./status ./logs

python3 -m venv ./.venv

./.venv/bin/pip install -r ./tool/requirements.txt

./.venv/bin/python ./tool/cli.py years >/dev/null

