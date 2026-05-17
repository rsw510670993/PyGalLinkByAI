#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

rm -f ./getchu.db
rm -rf ./.venv

mkdir -p ./status ./logs

python3 -m venv ./.venv

./.venv/bin/pip install -r ./tool/requirements.txt

./.venv/bin/python ./tool/cli.py years >/dev/null

if ! command -v aria2c >/dev/null 2>&1; then
  echo "提示：未检测到 aria2c。磁链校验页将无法获取元数据文件列表。可执行：sudo apt-get update && sudo apt-get install -y aria2"
fi
