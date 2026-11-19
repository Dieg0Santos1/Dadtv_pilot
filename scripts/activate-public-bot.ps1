$ErrorActionPreference = 'Stop'
$botRoot = 'C:\DadTvPlay-Pilot'
$serviceName = 'DadTvBot'
$envPath = Join-Path $botRoot 'backend\.env'
if (-not (Test-Path -LiteralPath $envPath)) { throw 'Falta backend\.env en la VM.' }
$envText = [System.IO.File]::ReadAllText($envPath)
$tokenMatch = [regex]::Match($envText, '(?m)^ADMIN_API_TOKEN=(.*)$')
if (-not $tokenMatch.Success) { throw 'Falta ADMIN_API_TOKEN.' }
$token = $tokenMatch.Groups[1].Value.Trim().Trim('"', "'")
$headers = @{ Authorization = "Bearer $token" }
$status = Invoke-RestMethod 'http://127.0.0.1:8000/admin/status' -Headers $headers -TimeoutSec 5
if ($status.provider -ne 'wasender' -or $status.send_enabled -ne $true) {
    throw 'El bot no esta listo para responder por WaSender.'
}
$counts = $status.wasender_webhook.counts
if ($counts.manual_pause -lt 1 -or $counts.manual_resume -lt 1) {
    throw 'Primero verifica /bot off y /bot on desde el WhatsApp del propietario.'
}
if ($envText -notmatch '(?m)^BOT_TEST_CONTACTS_ONLY=true[ \t]*\r?$') {
    throw 'Se esperaba BOT_TEST_CONTACTS_ONLY=true; revisa el estado actual.'
}
$backup = "$envPath.backup-public-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath $envPath -Destination $backup
try {
    $updated = [regex]::Replace($envText, '(?m)^BOT_TEST_CONTACTS_ONLY=true[ \t]*\r?$', 'BOT_TEST_CONTACTS_ONLY=false')
    [System.IO.File]::WriteAllText($envPath, $updated, [System.Text.UTF8Encoding]::new($false))
    Restart-Service -Name $serviceName
    $ready = $false
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2
            if ($health.ok -eq $true) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) { throw 'El bot no respondio en /health.' }
    $updatedStatus = Invoke-RestMethod 'http://127.0.0.1:8000/admin/status' -Headers $headers -TimeoutSec 5
    if ($updatedStatus.test_contacts_only -ne $false -or $updatedStatus.send_enabled -ne $true) {
        throw 'El bot no quedo abierto a todos los numeros.'
    }
    Write-Host "Bot abierto a todos los numeros; /health OK. Respaldo de configuracion: $backup"
} catch {
    Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $backup -Destination $envPath -Force
    Start-Service -Name $serviceName -ErrorAction SilentlyContinue
    throw
}
