$ErrorActionPreference = 'Stop'

$envPath = 'C:\DadTvPlay-Pilot\backend\.env'
$serviceName = 'DadTvBot'
if (-not (Test-Path -LiteralPath $envPath)) { throw 'No existe backend\.env en la VM.' }

$original = [System.IO.File]::ReadAllText($envPath)
function Read-Value([string] $name) {
    $match = [regex]::Match($original, "(?m)^$([regex]::Escape($name))=(.*)$")
    if (-not $match.Success) { throw "Falta $name en backend\.env." }
    return $match.Groups[1].Value.Trim().Trim('"', "'")
}

$backup = "$envPath.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath $envPath -Destination $backup
$updated = [regex]::Replace($original, '(?m)^BOT_COMMERCE_ENABLED=.*$', 'BOT_COMMERCE_ENABLED=false')
$updated = [regex]::Replace($updated, '(?m)^BOT_RENEWAL_ENABLED=.*$', 'BOT_RENEWAL_ENABLED=false')

try {
    [System.IO.File]::WriteAllText($envPath, $updated, [System.Text.UTF8Encoding]::new($false))
    Restart-Service -Name $serviceName
    $ready = $false
    for ($attempt = 0; $attempt -lt 25; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2
            if ($health.ok -eq $true -and $health.mode -eq 'live') { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) { throw 'El bot no respondio en /health despues del reinicio.' }
    $adminToken = Read-Value 'ADMIN_API_TOKEN'
    $status = Invoke-RestMethod 'http://127.0.0.1:8000/admin/status' -Headers @{ Authorization = "Bearer $adminToken" } -TimeoutSec 5
    if ($status.commerce_enabled -ne $false) { throw 'Las compras siguen activadas.' }
    Write-Host 'Prueba cerrada: compras y renovaciones desactivadas; /health OK.'
    Write-Host "Respaldo de la configuracion: $backup"
} catch {
    Copy-Item -LiteralPath $backup -Destination $envPath -Force
    Restart-Service -Name $serviceName -ErrorAction SilentlyContinue
    throw
}
