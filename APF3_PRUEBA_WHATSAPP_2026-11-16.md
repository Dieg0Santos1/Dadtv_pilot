# Prueba controlada APF3 por WhatsApp — 16 de noviembre de 2026

La prueba se realizó desde el contacto autorizado del piloto hacia el bot
en la VM Windows, con la versión del modelo documentada en
`APF3_CLASIFICADOR.md`. El dueño aportó capturas de la conversación y el
[log filtrado](evidence/apf3-ml-log-2026-11-16.txt) de `dadtv.bot`. Las
capturas aún no están anexadas a este repositorio: muestran nombre y foto de
perfil, que deben ocultarse antes de publicarlas o compartirlas en el informe.

El log marca UTC (`Z`); la conversación de WhatsApp muestra hora de Lima
(UTC−5). No se registraron instantes exactos de envío y entrega, así que
estas evidencias **no permiten calcular latencia de respuesta**.

| Mensaje | Intención / confianza / margen en log | Ruta real | Respuesta observada |
|---|---|---|---|
| `Hola, ¿qué planes tienen?` | `consulta` / 0,809 / 0,752 | `consulta` | Saludo y plataformas disponibles. |
| `premium` | `consulta` / 0,254 / 0,003 | `clarification` | Pidió aclarar la necesidad. |
| `Quiero HBO` | `pedido` / 0,806 / 0,742 | `pedido` | Ofreció cuatro opciones de HBO con precios y solicitó elección. |
| `cancelar` | `spam` / 0,314 / 0,114; sin vocabulario | Regla de cancelación, sin `route_ml` | Canceló la oferta pendiente. |
| `Venden ropa?` | `fuera_de_tema` / 0,638 / 0,422 | `fuera_de_tema` | Redirigió a productos digitales. |
| `Mi cuenta no funciona` | `reclamo` / 0,640 / 0,520 | `reclamo` | Derivó a atención humana e indicó pausa automática. |

Las cinco rutas registradas coinciden con las respuestas visibles. Eso
demuestra que el modelo entrenado se ejecutó dentro del flujo del MVP en
esta conversación controlada. Es una prueba funcional de seis mensajes,
no una estimación de precisión general ni una evaluación independiente
con clientes reales. La pausa se observa en el texto de la respuesta y en
la ruta del código; no se adjunta una consulta administrativa posterior de
estado.

En un ensayo anterior, el mensaje `Ya pagué` recibió una solicitud de monto
y comprobante, sin acreditación observada. Ese camino usa una regla específica
de pago antes del enrutamiento por clases; no se presenta como acierto del
clasificador ni como comprobación de un pago real. En ese ensayo anterior el
saludo repitió una pregunta; en esta segunda captura no apareció la repetición.

Para el informe, adjuntar las dos capturas compartidas por el dueño con
nombre y foto ocultos, más el archivo de log. No añadir números de teléfono,
credenciales ni comprobantes a este repositorio.
