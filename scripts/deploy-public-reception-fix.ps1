$ErrorActionPreference = 'Stop'
$botRoot = 'C:\DadTvPlay-Pilot'
$serviceName = 'DadTvBot'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$relativePaths = @(
    'backend\main.py',
    'backend\bot\wasender.py',
    'backend\bot\transport.py'
)
$envPath = Join-Path $botRoot 'backend\.env'
if (-not (Test-Path -LiteralPath $envPath)) { throw 'Falta backend\.env en la VM.' }
$envText = [System.IO.File]::ReadAllText($envPath)
if ($envText -notmatch '(?m)^BOT_TEST_CONTACTS_ONLY=false[ \t]*\r?$') {
    throw 'El bot debe seguir abierto a todos los numeros.'
}
if ((Get-Service -Name $serviceName).Status -ne 'Running') {
    throw 'DadTvBot debe estar en ejecucion antes de actualizar.'
}
$backupRoot = Join-Path $botRoot ("backup-public-reception-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
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
    $tokenMatch = [regex]::Match($envText, '(?m)^ADMIN_API_TOKEN=(.*)$')
    if (-not $tokenMatch.Success) { throw 'Falta ADMIN_API_TOKEN para verificar el estado.' }
    $token = $tokenMatch.Groups[1].Value.Trim().Trim('"', "'")
    $status = Invoke-RestMethod 'http://127.0.0.1:8000/admin/status' -Headers @{Authorization="Bearer $token"} -TimeoutSec 5
    if ($status.test_contacts_only -ne $false -or $status.send_enabled -ne $true) {
        throw 'La configuracion publica no quedo activa.'
    }
    Write-Host "Recepcion de numeros nuevos actualizada; todos los contactos siguen habilitados; /health OK. Respaldo: $backupRoot"
} catch {
    Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
    foreach ($relativePath in $relativePaths) {
        Copy-Item -LiteralPath (Join-Path $backupRoot $relativePath) -Destination (Join-Path $botRoot $relativePath) -Force
    }
    Start-Service -Name $serviceName -ErrorAction SilentlyContinue
    throw
}
