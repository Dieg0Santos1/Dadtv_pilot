# Cierre de evidencia APF3 en la VM

El paquete `dadtvplay-pilot-apf3-selected.zip` contiene solamente el modelo
seleccionado y dos scripts. No contiene `.env`, SQLite ni credenciales.

## 1. Instalar sin reanudar el chat

Copiar el ZIP a `C:\` de la VM. En PowerShell como administrador:

```powershell
Expand-Archive -LiteralPath 'C:\dadtvplay-pilot-apf3-selected.zip' -DestinationPath 'C:\DadTvAPF3Selected' -Force
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\DadTvAPF3Selected\scripts\deploy-apf3-selected.ps1'
```

El script exige que el único contacto autorizado siga pausado, respalda el
modelo previo, instala el JSON, reinicia `DadTvBot` y comprueba `/health`, las
seis clases y la pausa. Solo debe considerarse instalado si muestra
`Modelo APF3 actualizado; seis clases cargadas; /health OK; chat de prueba pausado`.
Conservar la ruta de respaldo por si hiciera falta revertir.

## 2. Prueba por WhatsApp, solo tras autorización del dueño

Reanudar **solo el contacto autorizado** desde el endpoint administrativo
`/admin/conversations/pause` con `paused=false`. No cambiar la lista de
contactos permitidos ni habilitarlo al público. Hacer una conversación breve
sin pagos, por ejemplo:

1. `Hola` (consulta).
2. `Quiero HBO` (pedido, sujeto al stock real).
3. `Venden ropa` (fuera de tema).
4. `Mi cuenta no funciona` (reclamo; el bot debe derivar y pausar el chat).

Registrar la respuesta **real** de cada mensaje, incluso si difiere de lo
esperado. Guardar la captura completa de WhatsApp. Antes de compartirla,
ocultar teléfonos, nombres, fotos de perfil y cualquier credencial.

Después de las respuestas, en PowerShell de la VM:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\DadTvAPF3Selected\scripts\collect-apf3-evidence.ps1'
```

Copiar `C:\DadTvBotEvidence\apf3-ml-log.txt` junto a la captura. El archivo
filtra las líneas `intent_ml`, `confidence`, `margin` y `route_ml`, sin cuerpos
de mensajes. Revisar visualmente el archivo antes de compartirlo. La captura
demuestra la respuesta recibida; el log muestra la predicción y la ruta. Si el
log no existe, el script lo indicará y habrá que revisar la salida de NSSM.

El reclamo final debe dejar el chat pausado. Confirmar la pausa en el panel
administrativo antes de terminar la prueba.
