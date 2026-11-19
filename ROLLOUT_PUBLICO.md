# Recargas y control manual del bot

## Causa del silencio de la captura

La conversaci�n de prueba estaba marcada como `human_paused=true`. Mientras est� pausada, el backend descarta los mensajes entrantes, incluidas las im�genes, antes de llamar a la funci�n que verifica comprobantes. No se acredita saldo por una imagen recibida en ese estado. Al reanudar, hay que iniciar de nuevo `recargar` y especificar el monto.

## Control desde el WhatsApp del propietario

Activar en WaSender las suscripciones `messages.received`, `message.sent` y `messages.upsert`, todas en la misma URL `/webhook/wasender` y con el mismo secreto. El evento `messages.upsert` incluye mensajes salientes enviados desde la aplicaci�n de WhatsApp. El propietario env�a estos textos exactos en un chat privado:

| Mensaje del propietario | Resultado |
|---|---|
| `bot off` | Pausa �nicamente ese chat y cancela las respuestas autom�ticas pendientes. |
| `bot on` | Reanuda �nicamente ese chat. |

Los comandos aparecen en la conversaci�n. Tambi�n se aceptan con una `/` inicial. Un mensaje normal del propietario no cambia el estado. El backend solo acepta el control si el evento saliente viene del webhook autenticado, es reciente y apunta a un chat privado. Al pausar tambi�n se borra el monto de recarga pendiente, de modo que no se use una captura contra una intenci�n anterior.

## Despliegue y prueba

1. Descomprimir el ZIP en `C:\DadTvPublicUpdate` y ejecutar `scripts\deploy-public-handover.ps1` como administrador. Este paso respalda los cinco archivos cambiados y `.env`, reinicia `DadTvBot` y deja temporalmente `BOT_TEST_CONTACTS_ONLY=true`, incluso si la VM estaba abierta a todos.
2. Marcar `message.sent` y `messages.upsert` en el webhook de la sesi�n DadTvPlay. Mantener `messages.received` marcado.
3. Desde el WhatsApp del propietario, enviar `bot on` al chat de prueba que estaba pausado. Confirmar que ese chat vuelve a responder. Luego enviar `bot off`, comprobar que no responde a un texto de prueba, y `bot on` otra vez.
4. Desde el n�mero de prueba, enviar `recargar` y el monto `20`. Enviar una foto de un objeto, sin comprobante. Esperar la respuesta `La imagen no parece comprobante` y verificar que no se acredit� saldo.
5. Solo cuando el control manual y el rechazo de la foto funcionen, ejecutar `scripts\activate-public-bot.ps1`. El script exige haber observado tanto `bot off` como `bot on` desde el �ltimo reinicio y cambia `BOT_TEST_CONTACTS_ONLY=false` con respaldo de `.env`.
6. Probar un saludo desde otro n�mero. Para mantener una conversaci�n personal en ese chat, el propietario env�a `bot off` antes de seguir conversando. Para devolver el chat al bot, env�a `bot on`.

Este cambio no modifica SQL ni la funci�n de verificaci�n de comprobantes. La acreditaci�n sigue condicionada a la verificaci�n existente. No repetir un Yape si una captura queda pendiente de revisi�n.
