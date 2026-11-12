# DadTvPlay — chatbot de ventas

Backend FastAPI para atender por WhatsApp y consultar el catálogo de **DadTvStore** en Supabase. La prueba actual usa WaSender y está restringida a un número autorizado. La recarga, compra y renovación reales se probaron con esa cuenta el 15 de octubre de 2026; comprueba el estado de la VM antes de cada demostración. La integración del clasificador APF3 se documenta en [APF3_CLASIFICADOR.md](APF3_CLASIFICADOR.md).

Para alojarlo en un servidor Windows con NSSM/WinSW y un proxy HTTPS fijo, consulta [WINDOWS_SERVER.md](WINDOWS_SERVER.md).

## Qué está implementado

- Catálogo real: servicios, configuración de precios y planes, cuentas, perfiles y ventas activas. Los productos ocultos no se ofrecen. Las lecturas de inventario incluyen paginación y no cargan contraseñas de streaming.
- Precios calculados por código, con descuentos premium y redondeo equivalente a la RPC de compra de la tienda.
- Conversación con reglas locales o interpretación y redacción con OpenAI Responses. Antes de responder se identifica al cliente por su número y se consulta su saldo. La IA recibe solo nombre, saldo, condición premium, catálogo público e historial; no recibe IDs, teléfono ni credenciales de streaming del backend.
- Respuestas naturales: ofrece la plataforma solicitada, indica si el saldo alcanza y cuánto falta, mantiene contexto para «el premium» o «el de tres meses», y responde a saludos y agradecimientos sin reiniciar el pedido.
- Redacción generativa con fragmentos verificables: la IA enlaza los datos en una respuesta conversacional; el backend inserta importes y la pregunta de compra. Se rechazan plantillas que omitan información obligatoria o agreguen cifras/afirmaciones de cobro. Si falla la redacción se utiliza un texto local con los mismos datos.
- Cotización con vigencia, confirmación explícita y compra con el saldo de un cliente ya registrado. La confirmación no depende del modelo.
- Compra mediante `bot_procesar_compra`, un wrapper nuevo e idempotente de `procesar_compra_tienda`: valida el número del cliente y revierte toda la operación si el total cambió.
- Renovación de una venta activa mediante `bot_renovar_venta`: vuelve a validar propiedad, fecha, precio y saldo dentro de una transacción. Se ofrece solo después de instalar `database/bot_renewal.sql`.
- Recepción por webhook POST de WaSender o Wamundo; Wamundo también admite hook GET + sondeo. Ignora grupos y deduplica mensajes antes de responder.
- Bandejas de entrada/salida y sesiones en SQLite con recuperación de leases, reintentos y cola de errores. Fragmentos de una conversación se agrupan tras unos segundos de silencio.
- Pausa para atención humana, historial administrativo y reanudación mediante API protegida.
- Demo local sin claves, cobros ni llamadas externas. Pruebas automatizadas con proveedores simulados.

## Demo desde Windows

La carpeta `.venv` ya se preparó localmente durante la implementación. Desde PowerShell:

```powershell
cd D:\CODE\dadtvplay-pilot
.\probar_bot.ps1
```

Si PowerShell restringe scripts, ejecuta directamente:

```powershell
.\.venv\Scripts\python.exe backend\scripts\demo_chat.py
```

Prueba `hola`, `Netflix perfil`, `confirmo`, `saldo`, `quiero un asesor`. Las credenciales y precios de la demo son ficticios. Al iniciar otra sesión de la demo se elimina el pedido pendiente y se reanuda la conversación de prueba.

También puedes probar `Hola, quiero comprar Disney` → `el de tres meses` → `gracias` → `sí, dale`. El bot consulta el cliente ficticio y su saldo, conserva el pedido y pide autorización antes de comprar. No necesitas escribir una palabra técnica de confirmación: reconoce respuestas afirmativas explícitas como «sí», «dale» y «sí, por favor»; una pregunta o frase ambigua no autoriza el cobro.

Para probar la redacción generativa real, configura `OPENAI_API_KEY` y `OPENAI_BOT_MODEL` en `backend/.env` y ejecuta:

```powershell
.\.venv\Scripts\python.exe backend\scripts\demo_chat.py --ai
```

Esta opción utiliza OpenAI y consume tokens de tu cuenta API. El catálogo, el cliente y las compras siguen siendo ficticios; no conecta Supabase ni Wamundo. Sin `--ai`, la demo utiliza reglas y textos locales. En el servidor, `BOT_ENGINE=openai` activa interpretación y redacción.

Modelo elegido para el bot: `gpt-5.4-mini-2026-03-17`, con interpretación de intención y redacción natural. El [precio publicado por OpenAI](https://developers.openai.com/api/docs/models/gpt-5.4-mini) es USD 0.75 por millón de tokens de entrada y USD 4.50 por millón de salida; cada mensaje puede usar dos llamadas y el costo real depende del catálogo y el historial. Esta versión figura en el [programa de tokens diarios por compartir entradas y salidas](https://help.openai.com/en/articles/10306912-sharing-feedback-evaluation-and-fine-tuning-data-and-api-inputs-and-outputs-with-openai), sujeto a elegibilidad, proyecto y cuota. Compartir datos de clientes para este beneficio requiere evaluar privacidad antes de abrir el bot a todos. La espera de agrupación se configuró en 3 segundos para las pruebas por WhatsApp.

Para reproducir el entorno en otra máquina con Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock
```

## Ejecutar el servidor local

Copiar `backend/.env.example` a `backend/.env`. Generar un token administrativo local y colocarlo en `ADMIN_API_TOKEN`; no compartir claves por chat ni guardarlas en Git. Mantener `BOT_MODE=demo`, `BOT_ENGINE=rules` y ambos switches de operaciones reales en `false`.

```powershell
cd D:\CODE\dadtvplay-pilot\backend
..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --env-file .env --workers 1 --no-access-log
```

`GET /health` indica que el proceso responde; no comprueba conectividad a proveedores. Las demás rutas administrativas requieren `Authorization: Bearer <ADMIN_API_TOKEN>`.

| Ruta | Uso |
|---|---|
| `POST /admin/demo/chat` | Demo: JSON con `message`, `phone` opcional y `message_id` opcional. Devuelve respuesta y estado. |
| `GET /admin/status` | Modo, switches y conteos de mensajes pendientes/fallidos. |
| `GET /admin/conversations/paused` | Conversaciones pendientes de atención humana. |
| `GET /admin/conversations/{phone}/history` | Últimos mensajes, sin credenciales salientes de las compras. |
| `GET /admin/conversations/{phone}/state` | Cotización pendiente y último identificador de pedido. |
| `POST /admin/conversations/pause` | JSON `{"phone":"519...","paused":true}`. Usar `false` para reanudar. |
| `POST /admin/queue/retry` | Reintentar mensajes fallidos después de corregir su causa. Puede reenviar un mensaje cuyo resultado de red fuera ambiguo. |

La solicitud de asesor guarda una conversación pausada; **no envía avisos al administrador**. El operador debe consultar la bandeja administrativa. Todavía no incluye una interfaz gráfica para asesores.

## Conectar el número de prueba

### Probar usando el WhatsApp existente de la tienda

Se puede mantener la cuenta de WhatsApp del negocio y limitar el chatbot a un contacto de prueba. El contacto permitido es quien **escribe al negocio**; no es el número desde el que responde el bot.

```env
BOT_TEST_CONTACTS_ONLY=true
BOT_ALLOWED_PHONES=51900000000
BOT_COMMERCE_ENABLED=false
```

La configuración local se preparó con este número de Perú. El filtro se aplica al webhook, al sondeo, a mensajes previamente encolados y al envío. Los mensajes nuevos de otros números se ignoran antes de guardarlos o consultar clientes/IA. Las pruebas locales de la demo no están sujetas a este filtro, pues no envían mensajes reales.

Sin contactos autorizados, el modo de pruebas bloquea la recepción y no permite activar envíos. `WASENDER_ACCOUNT_PHONE` identifica el número del negocio; `BOT_ALLOWED_PHONES` es el número del dueño que le escribe. No se vincula el WhatsApp del remitente mediante QR.

Las automatizaciones existentes de la tienda siguen independientes de este filtro. Revisar que no respondan por separado al mismo mensaje durante la prueba. Para abrir la atención a todos los clientes más adelante, configurar explícitamente `BOT_TEST_CONTACTS_ONLY=false`.

### WaSender: número existente de la tienda

1. En la sesión **DadTvPlay**, abrir **Manage Webhook** y revisar la URL y los eventos actuales. Guardar la configuración existente antes de cambiarla; un webhook ya usado por la tienda puede necesitar un reenviador para entregar el mismo evento al bot sin interrumpir su flujo.
2. Una vez desplegado el backend en una URL HTTPS, configurar `BOT_PROVIDER=wasender`, `WASENDER_ACCOUNT_PHONE=51900000001`, el token de la sesión en `WASENDER_API_TOKEN` y el secreto de webhook en `WASENDER_WEBHOOK_SECRET`. Nunca copiar esos secretos en chats o capturas.
3. Configurar el evento **messages.received** hacia `https://<backend>/webhook/wasender`. El backend verifica `X-Webhook-Signature`, ignora mensajes salientes y grupos, deduplica por ID y solo acepta `BOT_ALLOWED_PHONES=51900000000` durante pruebas. La documentación de WaSender muestra `data.messages` como objeto y `key.cleanedSenderPn` como teléfono del remitente.
4. Empezar con `BOT_COMMERCE_ENABLED=false` y `BOT_SEND_ENABLED=false`. Verificar una recepción de prueba en la cola; después activar envíos únicamente para el dueño. Las compras reales requieren la migración y validación aparte.

Referencias: [configuración de webhooks](https://www.wasenderapi.com/api-docs/webhooks/webhook-setup), [mensaje recibido](https://www.wasenderapi.com/api-docs/webhooks/webhook-message-received), [envío de texto](https://api.wasenderapi.com/api-docs/messages/send-text-message).

### Alternativa: Wamundo

1. Preparar un proyecto Supabase **de staging**, con la estructura y RPCs reales de la tienda y datos ficticios. Verificar la versión desplegada de `procesar_compra_tienda`; los SQL locales pueden diferir de producción.
2. Validar y ejecutar allí `database/bot_commerce.sql`. No ejecutar `supabase_schema.sql`: es el esquema histórico del piloto y conserva permisos abiertos.
3. Configurar `BOT_MODE=live`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `TENANT_ID` y un `ADMIN_API_TOKEN` largo. Mantener el comercio desactivado inicialmente.
4. Configurar `BOT_PROVIDER=wamundo`, vincular el número elegido en Wamundo y configurar `WAMUNDO_SECRET` y `WAMUNDO_ACCOUNT_ID`. Confirmar que el plan permite recepción/API/webhooks.
5. Probar el webhook POST `https://<backend>/webhook/wamundo` con el secret configurado en `WAMUNDO_WEBHOOK_SECRET`. Capturar en una prueba el `data.wid` de la cuenta receptora y configurarlo en `WAMUNDO_ACCOUNT_WID`; este valor NO es `account_unique`.
6. Si el webhook de tu cuenta no proporciona el contenido correctamente, usar el hook GET `/webhook/wamundo-hook?token=...` con `WAMUNDO_HOOK_TOKEN`, más el sondeo de respaldo. El bot responde al campo `reply_to`, nunca al campo `recipient` del mensaje recibido.
7. Activar `BOT_SEND_ENABLED=true` solo para este número de pruebas. Activar `BOT_ENGINE=openai`, `OPENAI_API_KEY` y `OPENAI_BOT_MODEL` cuando se quiera probar interpretación con IA. El modelo es configurable; en modo rules no se necesita ni se usa una clave.
8. Tras verificar catálogo, privacidad y recuperación, habilitar `BOT_COMMERCE_ENABLED=true` **en staging** y probar compras con saldo ficticio. El backend exige un canal de envío activo para habilitar cobros.

El webhook POST devuelve 200 después de persistir el mensaje. Si faltan secret o cuenta receptora, el acceso se rechaza o el mensaje se ignora. Un `WAMUNDO_ACCOUNT_ID` puede cambiar al volver a vincular WhatsApp.

Referencias: [webhooks de Wamundo](https://wamundo.com/docs/webhooks), [hooks GET](https://wamundo.com/docs/actions), [funciones de OpenAI](https://developers.openai.com/api/docs/guides/function-calling).

## Pago y entrega

El bot identifica un cliente por el número que entrega el proveedor, limitado a `TENANT_ID`. Si no está registrado, lo guía a `STORE_URL` para registrarse con ese número. No permite indicar otro teléfono o ID de cliente en el texto para acceder a su saldo.

Un contacto nuevo puede preguntar por el catálogo, pero el bot no inventa un saldo ni ejecuta compras. Para crear su cuenta le indica entrar a la tienda, pulsar «Ingresar / Registrar» y luego «Registrarme», usando el mismo WhatsApp. Después de completar el registro, puede escribir «ya me registré» o consultar su saldo; el bot vuelve a consultar la BD. El alta dentro del chat no está habilitada: la tienda ya recoge allí correo, nombre, teléfono y contraseña.

Si falta saldo, explica cómo recargar y mantiene la cotización hasta que venza. `RECHARGE_YAPE_PHONE`, `RECHARGE_RECEIVER`, `RECHARGE_MIN_AMOUNT` y `RECHARGE_WINDOW_MINUTES` deben coincidir con la tienda. `saldo` vuelve a consultar la BD. `confirmo` realiza la compra solo cuando `BOT_COMMERCE_ENABLED=true`.

### Recarga por WhatsApp (prueba limitada al número autorizado)

El bot recibe una captura de WaSender. El endpoint separado `DadTvStore/supabase/functions/verify-whatsapp-receipt/index.ts` acepta solo llamadas con credencial de servicio y utiliza la RPC `database/bot_recharge.sql`. La tienda sigue usando su propia `verify-receipt` sin cambios. La función exige `WHATSAPP_RECHARGE_TEST_PHONE=51900000000`, limita la acreditación automática a S/100 y rechaza imágenes claramente ajenas a pagos. No aplicar `DadTvStore/database/aprobar_recarga_automatica.sql`, que fue un borrador descartado.

Para permitir compra y renovación, instalar `database/bot_commerce.sql` y `database/bot_renewal.sql` en ese orden y comprobar ambas RPC antes de poner `BOT_COMMERCE_ENABLED=true`. La renovación tiene un interruptor propio, `BOT_RENEWAL_ENABLED`, que también empieza en `false`. No activar cobros reales hasta probar con saldo y una venta de prueba. El número autorizado no tiene actualmente una venta activa para ensayar renovación.

La tienda usa autenticación propia de clientes y su función de verificación confía en el `userId` del navegador. Un comprobante tampoco demuestra por sí solo que el dinero llegó a Yape; conviene conciliarlo antes de escalar la acreditación automática.

Si se pierde la respuesta de una compra, el siguiente intento recupera el mismo pedido remoto antes de revisar saldo o vencimiento. Si cambia el total, la transacción se revierte y se pide una cotización nueva. Los mensajes salientes se reintentan sin repetir la compra.

## Operación continua

- Una instancia, un worker y un disco persistente para SQLite. No usar múltiples procesos ni réplicas con esta implementación. Para escalar, migrar la cola a Postgres o un broker con locks distribuidos.
- `render.yaml` prepara una instancia `starter` con disco `/var/data`; arranca en demo y con operaciones reales desactivadas. **No se ha desplegado.** Revisar el costo antes de usar el blueprint.
- Render utiliza un sistema de archivos efímero salvo que se configure un disco. La cola perdería sus datos en un reinicio sin él. [Discos persistentes](https://render.com/docs/disks).
- Mantener apagados los access logs de Uvicorn para que los tokens del hook GET no aparezcan en logs. Los logs del bot muestran IDs locales y clases de error, sin cuerpos ni secretos.
- Proteger SQLite y las copias de seguridad: contienen teléfonos, conversaciones y mensajes aún no enviados. Las credenciales salientes se borran del spool después de la aceptación; pueden persistir en páginas/WAL/backups. El resultado de compra en `bot_orders` también contiene credenciales y solo debe ser accesible al backend. Definir retención y copias cifradas antes de producción.
- Revisar periódicamente `/admin/status`: después de cinco intentos, un mensaje pasa a `failed`. Una conversación con una recepción o envío fallido se mantiene bloqueada para no desordenar respuestas. La pausa administrativa no revierte una compra que ya estaba ejecutándose; revisar `bot_orders` para conciliar ese caso.
- Un HTTP 200 de Wamundo no basta: se comprueba el estado lógico de la respuesta. Esto confirma **aceptación por el proveedor**, no entrega al teléfono. Un timeout después de una aceptación puede generar una respuesta duplicada al reintentar; la venta mantiene el mismo identificador.
- El sondeo consulta hasta 30 mensajes; no implementa una paginación no documentada. Para tráfico continuo, comprobar webhook y recuperación de historial con soporte de Wamundo. La advertencia del límite debe investigarse.
- La antigüedad máxima solo filtra historial recibido del proveedor. Los mensajes ya guardados se conservan para recuperación.

Antes de habilitar dinero real, verificar en staging compras simultáneas de cuenta y perfil, convivencia con ventas manuales, stock agotado, descuentos, cambio de precio, teléfono duplicado, fallo de envío y timeout posterior al cobro. La RPC existente de la tienda sigue siendo responsable de la asignación de inventario; este wrapper no modifica sus reglas ni coordina las escrituras del gestor manual.

## Verificación local

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check backend\bot backend\main.py backend\tests backend\scripts\demo_chat.py
```

Los tests simulan Supabase, OpenAI y Wamundo: no hacen cobros, no envían mensajes ni llaman modelos reales. Validan también la sintaxis SQL y PL/pgSQL de la migración; **no sustituyen su ejecución contra un esquema de staging**.

Los módulos antiguos `classifier.py`, `store.py`, `responder.py`, `confirmation.py`, `config.py` y `wamundo_client.py` permanecen como referencia del piloto anterior; el nuevo `main.py` no los importa. `requirements-legacy.txt` permite reentrenar aquel clasificador por separado.
