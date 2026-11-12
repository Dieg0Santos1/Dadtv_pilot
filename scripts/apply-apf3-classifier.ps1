$ErrorActionPreference = 'Stop'
$botRoot = 'C:\DadTvPlay-Pilot'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$serviceName = 'DadTvBot'
$relativePaths = @(
    'backend\main.py',
    'backend\bot\engine.py',
    'backend\bot\intent_model.py',
    'backend\bot\model\intent_apf3.json',
    'backend\bot\service.py',
    'backend\bot\settings.py'
)
$envPath = Join-Path $botRoot 'backend\.env'
if (-not (Test-Path -LiteralPath $envPath)) { throw 'Falta backend\.env en la VM.' }
if ((Get-Service -Name $serviceName).Status -ne 'Running') { throw 'DadTvBot debe estar ejecutandose antes de actualizar.' }
$envText = [System.IO.File]::ReadAllText($envPath)
if ($envText -match '(?m)^BOT_INTENT_MODEL_ENABLED\s*=\s*false\s*$') {
    throw 'BOT_INTENT_MODEL_ENABLED=false desactiva el clasificador. Corrige esa linea en backend\.env antes de actualizar.'
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = Join-Path $botRoot "backup-apf3-$stamp"
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$previous = @{}
foreach ($relative in $relativePaths) {
    $source = Join-Path $sourceRoot $relative
    $target = Join-Path $botRoot $relative
    if (-not (Test-Path -LiteralPath $source)) { throw "Falta en el paquete: $relative" }
    $previous[$relative] = Test-Path -LiteralPath $target
    if ($previous[$relative]) {
        $backup = Join-Path $backupRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
        Copy-Item -LiteralPath $target -Destination $backup
    }
}

try {
    Stop-Service -Name $serviceName
    foreach ($relative in $relativePaths) {
        $source = Join-Path $sourceRoot $relative
        $target = Join-Path $botRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $target -Force
        if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) {
            throw "No se pudo verificar $relative"
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
    if (-not $ready) { throw 'El bot no respondio en /health tras actualizar.' }
    $tokenMatch = [regex]::Match($envText, '(?m)^ADMIN_API_TOKEN=(.*)$')
    if (-not $tokenMatch.Success) { throw 'Falta ADMIN_API_TOKEN en backend\.env.' }
    $adminToken = $tokenMatch.Groups[1].Value.Trim().Trim('"', "'")
    $status = Invoke-RestMethod 'http://127.0.0.1:8000/admin/status' -Headers @{ Authorization = "Bearer $adminToken" } -TimeoutSec 5
    if ($status.intent_model_enabled -ne $true -or $status.intent_model_classes.Count -ne 6) {
        throw 'El clasificador APF3 no aparece cargado en /admin/status.'
    }
    Write-Host 'APF3 activo: seis clases cargadas; /health OK.'
    Write-Host "Respaldo de codigo: $backupRoot"
} catch {
    Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
    foreach ($relative in $relativePaths) {
        $target = Join-Path $botRoot $relative
        if ($previous[$relative]) {
            Copy-Item -LiteralPath (Join-Path $backupRoot $relative) -Destination $target -Force
        } elseif (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Force
        }
    }
    Start-Service -Name $serviceName -ErrorAction SilentlyContinue
    throw
}
