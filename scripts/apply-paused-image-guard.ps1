$ErrorActionPreference = 'Stop'
$botRoot = 'C:\DadTvPlay-Pilot'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $sourceRoot 'backend\bot\service.py'
$target = Join-Path $botRoot 'backend\bot\service.py'
$serviceName = 'DadTvBot'

if (-not (Test-Path -LiteralPath $source) -or -not (Test-Path -LiteralPath $target)) {
    throw 'Falta el archivo service.py en el paquete o en la instalacion.'
}
$backup = "$target.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath $target -Destination $backup

try {
    Stop-Service -Name $serviceName
    Copy-Item -LiteralPath $source -Destination $target -Force
    if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) {
        throw 'No se pudo verificar service.py.'
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
    if (-not $ready) { throw 'El bot no respondio en /health despues de actualizar.' }
    Write-Host 'Proteccion de capturas durante la pausa instalada; /health OK.'
    Write-Host "Respaldo: $backup"
} catch {
    Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $backup -Destination $target -Force
    Start-Service -Name $serviceName -ErrorAction SilentlyContinue
    throw
}
