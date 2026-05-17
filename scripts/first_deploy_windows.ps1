$ErrorActionPreference = "Stop"

Set-Location "$PSScriptRoot\.."

Remove-Item -Force -ErrorAction SilentlyContinue .\getchu.db
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue .\.venv

New-Item -ItemType Directory -Force -ErrorAction SilentlyContinue .\status, .\logs | Out-Null

python -m venv .\.venv

.\.venv\Scripts\pip install -r .\tool\requirements.txt

.\.venv\Scripts\python .\tool\cli.py years > $null

if (-not (Get-Command aria2c -ErrorAction SilentlyContinue)) {
    Write-Host "提示：未检测到 aria2c。磁链校验页将无法获取元数据文件列表。可使用 winget 安装：winget install aria2"
}
