$ErrorActionPreference = 'Stop'
$botRoot = 'C:\DadTvPlay-Pilot'
$serviceName = 'DadTvBot'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$relativeModel = 'backend\bot\model\intent_apf3.json'
$source = Join-Path $sourceRoot $relativeModel
$target = Join-Path $botRoot $relativeModel
$envPath = Join-Path $botRoot 'backend\.env'

if (-not (Test-Path -LiteralPath $source)) { throw 'Falta el modelo en el paquete.' }
if (-not (Test-Path -LiteralPath $target)) { throw 'Falta el modelo actual en la VM.' }
if (-not (Test-Path -LiteralPath $envPath)) { throw 'Falta backend\.env en la VM.' }
if ((Get-Service -Name $serviceName).Status -ne 'Running') { throw 'DadTvBot no esta ejecutandose.' }

$envText = [System.IO.File]::ReadAllText($envPath)
$tokenMatch = [regex]::Match($envText, '(?m)^ADMIN_API_TOKEN=(.*)$')
$phoneMatch = [regex]::Match($envText, '(?m)^BOT_ALLOWED_PHONES=(.*)$')
if (-not $tokenMatch.Success -or -not $phoneMatch.Success) { throw 'Faltan valores administrativos o el contacto de prueba.' }
$token = $tokenMatch.Groups[1].Value.Trim().Trim('"', "'")
$phones = @($phoneMatch.Groups[1].Value.Trim().Trim('"', "'").Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($phones.Count -ne 1) { throw 'El despliegue controlado requiere exactamente un contacto autorizado.' }
$headers = @{ Authorization = "Bearer $token" }
$paused = @(Invoke-RestMethod 'http://127.0.0.1:8000/admin/conversations/paused' -Headers $headers -TimeoutSec 5)
if ($phones[0] -notin @($paused | ForEach-Object { $_.phone })) { throw 'El contacto de prueba debe estar pausado antes de actualizar.' }

$backup = Join-Path $botRoot ("backup-apf3-model-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
Copy-Item -LiteralPath $target -Destination $backup
try {
    Stop-Service -Name $serviceName
    Copy-Item -LiteralPath $source -Destination $target -Force
    $expectedHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expectedHash) {
        throw 'La copia del modelo no coincide con el paquete.'
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
    $status = Invoke-RestMethod 'http://127.0.0.1:8000/admin/status' -Headers $headers -TimeoutSec 5
    if ($status.intent_model_enabled -ne $true -or $status.intent_model_classes.Count -ne 6) {
        throw 'El clasificador no aparece cargado en /admin/status.'
    }
    $pausedAfter = @(Invoke-RestMethod 'http://127.0.0.1:8000/admin/conversations/paused' -Headers $headers -TimeoutSec 5)
    if ($phones[0] -notin @($pausedAfter | ForEach-Object { $_.phone })) {
        throw 'El contacto de prueba ya no figura pausado.'
    }
    Write-Host "Modelo APF3 actualizado; seis clases cargadas; /health OK; chat de prueba pausado. SHA256: $expectedHash"
    Write-Host "Respaldo: $backup"
} catch {
    Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $backup -Destination $target -Force
    Start-Service -Name $serviceName -ErrorAction SilentlyContinue
    throw
}
