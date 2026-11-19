$ErrorActionPreference = 'Stop'
$botRoot = 'C:\DadTvPlay-Pilot'
$serviceName = 'DadTvBot'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$relativePaths = @(
    'backend\main.py',
    'backend\bot\wasender.py',
    'backend\bot\repository.py',
    'backend\bot\service.py',
    'backend\bot\settings.py'
)
$envPath = Join-Path $botRoot 'backend\.env'
if (-not (Test-Path -LiteralPath $envPath)) { throw 'Falta backend\.env en la VM.' }
if ((Get-Service -Name $serviceName).Status -ne 'Running') { throw 'DadTvBot debe estar en ejecucion.' }
$envText = [System.IO.File]::ReadAllText($envPath)
if ($envText -notmatch '(?m)^BOT_TEST_CONTACTS_ONLY=true[ \t]*$') {
    throw 'El despliegue inicial requiere BOT_TEST_CONTACTS_ONLY=true.'
}
$backupRoot = Join-Path $botRoot ("backup-public-handover-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backupRoot | Out-Null
foreach ($relativePath in $relativePaths) {
    $source = Join-Path $sourceRoot $relativePath
    $target = Join-Path $botRoot $relativePath
    if (-not (Test-Path -LiteralPath $source) -or -not (Test-Path -LiteralPath $target)) {
        throw "Falta archivo: $relativePath"
    }
    $backup = Join-Path $backupRoot $relativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
    Copy-Item -LiteralPath $target -Destination $backup
}
try {
    Stop-Service -Name $serviceName
    foreach ($relativePath in $relativePaths) {
        $source = Join-Path $sourceRoot $relativePath
        $target = Join-Path $botRoot $relativePath
        Copy-Item -LiteralPath $source -Destination $target -Force
        if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) {
            throw "La copia no coincide: $relativePath"
        }
    }
    Start-Service -Name $serviceName
    $ready = $false
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2
            if ($health.ok -eq $true -and $health.mode -eq 'live') { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) { throw 'El bot no respondio en /health.' }
    Write-Host "Codigo actualizado; /health OK; contactos aun restringidos. Respaldo: $backupRoot"
} catch {
    Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
    foreach ($relativePath in $relativePaths) {
        Copy-Item -LiteralPath (Join-Path $backupRoot $relativePath) -Destination (Join-Path $botRoot $relativePath) -Force
    }
    Start-Service -Name $serviceName -ErrorAction SilentlyContinue
    throw
}
