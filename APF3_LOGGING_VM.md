# Activar el log APF3 en la VM

El primer ensayo por WhatsApp demostró respuestas, pero `DadTvBot` no tenía
`AppStderr` de NSSM configurado y el logger `dadtv.bot` no emitía mensajes
`INFO`. Por eso no se recuperan las predicciones del primer ensayo. Hay que
registrar una segunda conversación controlada; no se deben fabricar logs.

Copiar `dadtvplay-pilot-apf3-logging.zip` a `C:\` de la VM. En PowerShell como
administrador:

```powershell
Expand-Archive -LiteralPath 'C:\dadtvplay-pilot-apf3-logging.zip' -DestinationPath 'C:\DadTvAPF3Logging' -Force
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\DadTvAPF3Logging\scripts\enable-apf3-logging.ps1'
```

El script exige que el contacto autorizado esté pausado, respalda `main.py`,
configura `AppStdout` y `AppStderr`, reinicia el servicio y comprueba `/health`,
las seis clases y la pausa. El éxito se indica con `Registro APF3 activo;
/health OK; seis clases; chat pausado.`. La ruta del log aparece en pantalla.

Solo entonces se reanuda el contacto de prueba y se repite la secuencia de
mensajes de `APF3_PRUEBA_VM.md`, sin pagos. El reclamo final debe volver a
pausar el chat. Recoger las líneas con:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\DadTvAPF3Logging\scripts\collect-apf3-evidence.ps1'
```

Revisar `C:\DadTvBotEvidence\apf3-ml-log.txt` antes de compartirlo: debe
contener `intent_ml`, `confidence`, `margin` y `route_ml`. Para `cancelar` y
`Ya pagué`, reglas comerciales previas pueden actuar sin `route_ml`; deben
explicarse como excepciones, nunca como clasificación correcta de pago.
