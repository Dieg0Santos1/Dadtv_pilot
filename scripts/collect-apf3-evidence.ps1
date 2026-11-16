$ErrorActionPreference = 'Stop'
$nssm = 'C:\nssm-2.24\win64\nssm.exe'
$service = 'DadTvBot'
$outputDir = 'C:\DadTvBotEvidence'
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

$logPath = ''
if (Test-Path -LiteralPath $nssm) {
    $logPath = (& $nssm get $service AppStderr | Select-Object -First 1).Trim()
}
if (-not $logPath) { $logPath = 'C:\DadTvBotData\bot.stderr.log' }
if (-not (Test-Path -LiteralPath $logPath)) {
    throw "No se encontro el log de errores del servicio en $logPath. Revisa NSSM AppStderr."
}

$patterns = 'intent_ml=|route_ml='
$lines = @(Get-Content -LiteralPath $logPath -Tail 500 | Select-String $patterns | Select-Object -Last 30 | ForEach-Object { $_.Line })
if ($lines.Count -eq 0) { throw 'No hay predicciones APF3 recientes en el log.' }
$report = @(
    "Recogido: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))",
    "Origen: $logPath",
    'Solo lineas de intencion y ruta; no incluye cuerpos de mensajes.'
) + $lines
$path = Join-Path $outputDir 'apf3-ml-log.txt'
$report | Set-Content -LiteralPath $path -Encoding UTF8
Write-Host "Evidencia de log: $path"
