$ErrorActionPreference = 'Stop'
$root = 'C:\DadTvPlay-Pilot'
$service = 'DadTvBot'
$nssm = 'C:\nssm-2.24\win64\nssm.exe'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $sourceRoot 'backend\main.py'
$target = Join-Path $root 'backend\main.py'
$envPath = Join-Path $root 'backend\.env'
$logDir = 'C:\DadTvBotEvidence'
$stdout = Join-Path $logDir 'bot.stdout.log'
$stderr = Join-Path $logDir 'bot.stderr.log'

foreach ($path in @($nssm, $source, $target, $envPath)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Falta: $path" }
}
if ((Get-Service -Name $service).Status -ne 'Running') { throw 'DadTvBot debe estar ejecutandose.' }
$envText = [System.IO.File]::ReadAllText($envPath)
$tokenMatch = [regex]::Match($envText, '(?m)^ADMIN_API_TOKEN=(.*)$')
$phoneMatch = [regex]::Match($envText, '(?m)^BOT_ALLOWED_PHONES=(.*)$')
if (-not $tokenMatch.Success -or -not $phoneMatch.Success) { throw 'Falta token administrativo o contacto permitido.' }
$token = $tokenMatch.Groups[1].Value.Trim().Trim('"', "'")
$phones = @($phoneMatch.Groups[1].Value.Trim().Trim('"', "'").Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($phones.Count -ne 1) { throw 'Se requiere un solo contacto autorizado.' }
$headers = @{ Authorization = "Bearer $token" }
$paused = @(Invoke-RestMethod 'http://127.0.0.1:8000/admin/conversations/paused' -Headers $headers -TimeoutSec 5)
if ($phones[0] -notin @($paused | ForEach-Object { $_.phone })) {
    throw 'El contacto de prueba debe estar pausado antes de configurar logs.'
}

$oldStdout = (& $nssm get $service AppStdout | Out-String).Trim()
$oldStderr = (& $nssm get $service AppStderr | Out-String).Trim()
$backup = Join-Path $root ("backup-apf3-logging-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.py')
Copy-Item -LiteralPath $target -Destination $backup
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

try {
    Stop-Service -Name $service
    Copy-Item -LiteralPath $source -Destination $target -Force
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
        throw 'La copia de main.py no coincide con el paquete.'
    }
    & $nssm set $service AppStdout $stdout | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'NSSM no pudo configurar AppStdout.' }
    & $nssm set $service AppStderr $stderr | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'NSSM no pudo configurar AppStderr.' }
    & $nssm set $service AppRotateFiles 1 | Out-Null
    Start-Service -Name $service
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
        throw 'No aparecen las seis clases en /admin/status.'
    }
    $pausedAfter = @(Invoke-RestMethod 'http://127.0.0.1:8000/admin/conversations/paused' -Headers $headers -TimeoutSec 5)
    if ($phones[0] -notin @($pausedAfter | ForEach-Object { $_.phone })) {
        throw 'El contacto de prueba ya no esta pausado.'
    }
    $loggingReady = $false
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        if ((Test-Path -LiteralPath $stderr) -and
            (Get-Content -LiteralPath $stderr -Tail 25 | Select-String -SimpleMatch 'Bot iniciado')) {
            $loggingReady = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $loggingReady) { throw 'NSSM no registro el inicio del bot en AppStderr.' }
    Write-Host 'Registro APF3 activo; /health OK; seis clases; chat pausado.'
    Write-Host "Log: $stderr"
    Write-Host "Respaldo: $backup"
} catch {
    Stop-Service -Name $service -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $backup -Destination $target -Force
    if ($oldStdout) { & $nssm set $service AppStdout $oldStdout | Out-Null }
    else { & $nssm reset $service AppStdout | Out-Null }
    if ($oldStderr) { & $nssm set $service AppStderr $oldStderr | Out-Null }
    else { & $nssm reset $service AppStderr | Out-Null }
    Start-Service -Name $service -ErrorAction SilentlyContinue
    throw
}
