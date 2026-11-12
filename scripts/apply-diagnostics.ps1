$ErrorActionPreference = 'Stop'
$botRoot = 'C:\DadTvPlay-Pilot'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$serviceName = 'DadTvBot'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$paths = @('backend\main.py', 'backend\bot\wasender.py')
$backups = @()

foreach ($relativePath in $paths) {
    $source = Join-Path $sourceRoot $relativePath
    $target = Join-Path $botRoot $relativePath
    if (-not (Test-Path -LiteralPath $source) -or -not (Test-Path -LiteralPath $target)) {
        throw "Falta un archivo: $relativePath"
    }
    $backup = "$target.backup-$stamp"
    Copy-Item -LiteralPath $target -Destination $backup
    $backups += @{ Source = $source; Target = $target; Backup = $backup }
}

try {
    Stop-Service -Name $serviceName
    foreach ($item in $backups) {
        Copy-Item -LiteralPath $item.Source -Destination $item.Target -Force
    }
    Start-Service -Name $serviceName
    $healthy = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $response = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2
            if ($response.ok -eq $true) { $healthy = $true; break }
        } catch { }
    }
    if (-not $healthy) { throw 'El bot no respondio en /health tras la actualizacion.' }
    Write-Host 'Bot actualizado y /health responde OK.'
} catch {
    Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
    foreach ($item in $backups) {
        Copy-Item -LiteralPath $item.Backup -Destination $item.Target -Force
    }
    Start-Service -Name $serviceName -ErrorAction SilentlyContinue
    throw
}
