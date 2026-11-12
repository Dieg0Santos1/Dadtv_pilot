# Bot de WhatsApp en servidor Windows

Esta guía se ejecuta **dentro de la máquina virtual Windows** donde ya administras tus servicios. Usa el mismo WaSender de la tienda, pero el bot responde solo a `51900000000` mientras esté en prueba. El servicio escucha en `127.0.0.1:8000`. Como la VM solo es accesible en la red local por `192.168.1.140`, necesita una URL HTTPS pública para recibir webhooks de WaSender.

Si no recuerdas qué herramienta publica tus webs, ejecuta `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows-host.ps1` en la VM. Solo muestra puertos, nombres de procesos y servicios, sin argumentos ni claves.

## Archivos y entorno

1. Copiar `dadtvplay-pilot-deploy.zip` **a la VM** y descomprimirlo en una carpeta para el servicio, por ejemplo `C:\Services\dadtvplay-pilot`. Abrir PowerShell en esa carpeta. Instalar Python 3.12 y ejecutar:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock
   ```

2. Crear `backend\.env` en el servidor copiando de forma segura los valores actuales; nunca incluirlo en el ZIP del código ni en Git. Mantener:

   ```env
   BOT_MODE=live
   BOT_PROVIDER=wasender
   BOT_TEST_CONTACTS_ONLY=true
   BOT_ALLOWED_PHONES=51900000000
   BOT_SEND_ENABLED=true
   BOT_COMMERCE_ENABLED=false
   BOT_RENEWAL_ENABLED=false
   BOT_WHATSAPP_RECHARGE_ENABLED=true
   BOT_DATABASE_PATH=C:\DadTvBotData\bot.sqlite3
   ```

   Crear `C:\DadTvBotData` y conceder lectura/escritura a la cuenta del servicio. Puedes elegir otras rutas de la VM; ajusta `BOT_DATABASE_PATH` y el servicio en consecuencia. Conservar los demás secretos (`SUPABASE_SERVICE_ROLE_KEY`, WaSender, OpenAI y webhook) solo en ese `.env`.

3. Probar el lanzador en primer plano desde la raíz:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1 -Port 8000
   ```

   Desde otra consola, `Invoke-WebRequest http://127.0.0.1:8000/health` debe responder `200`. Detener la consola de prueba antes de instalar el servicio.

## Servicio del bot

Con **NSSM**, estando en la carpeta donde se descomprimió el bot:

```powershell
$botRoot = (Get-Location).Path
nssm install DadTvBot powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$botRoot\scripts\start-windows.ps1`" -Port 8000"
nssm set DadTvBot AppDirectory $botRoot
nssm set DadTvBot AppExit Default Restart
nssm set DadTvBot AppStdout 'C:\DadTvBotData\bot.stdout.log'
nssm set DadTvBot AppStderr 'C:\DadTvBotData\bot.stderr.log'
nssm set DadTvBot AppRotateFiles 1
nssm start DadTvBot
```

Con **WinSW**, configurar el ejecutable `powershell.exe`, los mismos argumentos del lanzador, el directorio de trabajo en la raíz del bot y reinicio automático ante fallos. El supervisor debe ejecutar el lanzador en primer plano; no usar `Start-Process` dentro del servicio.

## URL HTTPS pública sin cambiar el DNS de la tienda

La opción inmediata es **Tailscale Funnel**. Da a esta VM una URL estable `https://<equipo>.<tailnet>.ts.net` y funciona aunque solo tenga IP privada; no requiere abrir puertos del router ni cambiar los dominios de GruposDigitales.

1. Instalar Tailscale **en la VM**, iniciar sesión y activar **Run unattended** en Preferences para que siga conectado después de cerrar la sesión de Windows. En el panel de Tailscale, dejar MagicDNS y certificados HTTPS activos. Revisar también la caducidad de la clave del dispositivo para que el servidor no exija autenticación interactiva inesperada.
2. Con el servicio `DadTvBot` ya respondiendo en `http://127.0.0.1:8000/health`, abrir PowerShell **como administrador** en la VM y ejecutar:

   ```powershell
   tailscale funnel --bg 8000
   tailscale funnel status
   ```

   La primera orden puede abrir una página para habilitar Funnel en la cuenta. Guardar la URL HTTPS que muestra `status`. `--bg` mantiene la publicación al reiniciar Tailscale o Windows. En Windows, el servicio de Tailscale y **Run unattended** deben estar activos.
3. Desde un celular con datos móviles, abrir `https://<equipo>.<tailnet>.ts.net/health` y comprobar `200` antes de cambiar WaSender. Luego configurar **solo** `Payload URL` como `https://<equipo>.<tailnet>.ts.net/webhook/wasender`; conservar el secreto y el evento `messages.received`. No pegar claves ni tokens en el chat.

Tailscale Funnel solo ofrece hostnames `*.ts.net` y tiene límites de ancho de banda; probar su estabilidad antes de considerarlo definitivo. Si después quieres `bot.programmersa.com`, primero habrá que elegir una publicación compatible con tu DNS y verificar todos los registros existentes antes de cambiar los servidores de nombres de GruposDigitales. No cambiar el DNS de `dadtvplay.com` para este piloto.

## Cambio desde la prueba local

Detener el bot local antes de activar el servicio para evitar dos workers. Si se desea conservar la conversación de prueba, hacer una copia consistente de SQLite con `sqlite3.Connection.backup()` mientras el proceso local esté detenido y colocarla en `BOT_DATABASE_PATH` del servidor; si no, iniciar con una base nueva. No copiar únicamente el archivo `.sqlite3` mientras el proceso escribe porque puede existir un WAL pendiente.

Tras el cambio, enviar un mensaje desde `51900000000` y confirmar respuesta y `GET /health`. Mantener compras y renovaciones reales apagadas hasta comprobar recarga con un pago nuevo, compra con saldo e inventario de prueba y renovación de una venta activa. El túnel `trycloudflare.com` se puede retirar una vez que WaSender use el subdominio estable.
