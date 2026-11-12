param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $repoPath 'backend'
$pythonPath = Join-Path $repoPath '.venv\Scripts\python.exe'
$envPath = Join-Path $backendPath '.env'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Falta el entorno Python en $pythonPath"
}
if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Falta backend/.env en $envPath"
}

Set-Location -LiteralPath $backendPath
& $pythonPath -m uvicorn main:app --host 127.0.0.1 --port $Port --env-file $envPath --workers 1 --no-access-log
exit $LASTEXITCODE
