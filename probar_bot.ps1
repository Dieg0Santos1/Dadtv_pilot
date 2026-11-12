# Demo sin claves, conexión a la BD ni envíos de WhatsApp.
$botPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$botDemoScript = Join-Path $PSScriptRoot 'backend\scripts\demo_chat.py'
if (-not (Test-Path -LiteralPath $botPython)) {
    throw 'Primero crea .venv e instala las dependencias según README.md.'
}
& $botPython $botDemoScript @args
