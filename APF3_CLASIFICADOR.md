# Clasificador entrenado dentro del bot

El backend procesa cada texto con un modelo TF-IDF de palabras (unigramas y bigramas) y regresión logística entrenado con las 180 frases etiquetadas de `backend/data/training_data.csv`. Predice una de seis clases: `consulta`, `pedido`, `reclamo`, `fuera_de_tema`, `ofensivo` y `spam`. OpenAI sigue redactando o proponiendo detalles de catálogo cuando corresponde; no sustituye al clasificador.

El entrenamiento está en `backend/train_apf3_classifier.py` y usa scikit-learn solo fuera del servidor. Exporta vocabulario, pesos e IDF a `backend/bot/model/intent_apf3.json`. `backend/bot/intent_model.py` ejecuta ese modelo dentro del servicio con la biblioteca estándar de Python. No se carga un pickle en producción.

El resultado reproducible está en `backend/bot/model/intent_apf3_report.json`: 144 ejemplos de entrenamiento y 36 reservados para evaluación. Acertó 26 de 36 (72,2 %); la validación cruzada de cuatro particiones **solo en entrenamiento** promedió 78,5 %. Durante el desarrollo se compararon configuraciones mirando una partición de prueba, por lo que el 72,2 % es descriptivo y podría ser optimista. Es un conjunto pequeño y manual; la cifra no demuestra precisión en clientes reales. La confianza tampoco está calibrada como probabilidad real de acierto.

El bot exige confianza mínima de 0,45, margen mínimo de 0,15 entre las dos clases principales y al menos un término conocido. Si falla alguna condición, pide aclaración sin cotizar ni derivar automáticamente. Los umbrales se configuran en `BOT_INTENT_CONFIDENCE_THRESHOLD` y `BOT_INTENT_MARGIN_THRESHOLD`. El servicio registra `intent_ml`, `confidence`, `margin` y `route_ml` sin añadir el texto del mensaje a esas líneas de log; `/admin/status` informa si el modelo está cargado.

`pedido` conduce al catálogo o cotización; `consulta` conserva una respuesta informativa y evita que una sugerencia de OpenAI se transforme en compra; `reclamo` pausa el chat y lo deriva a una persona; las otras tres clases reciben una respuesta breve. Las confirmaciones explícitas y elecciones de una cotización en curso siguen sus reglas y estado. El modelo clasifica el texto, pero jamás confirma un pago: «ya pagué» solo solicita datos y captura; una imagen solo acredita saldo si `verify-whatsapp-receipt` y la RPC de recarga devuelven aprobación válida.

Para reentrenar en el equipo de desarrollo:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-training.txt
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe backend\train_apf3_classifier.py
```

Para demostrarlo sin dinero real, iniciar el backend en modo demo y enviar «quiero netflix», «no puedo entrar a netflix», «venden ropa», «eres un inútil», «test» y una frase ambigua como «premium». Comparar las respuestas y los registros `intent_ml`/`route_ml`. La prueba automatizada `backend/tests/test_intent_model.py` comprueba además que el enrutamiento usa la clase incluso cuando el asistente propone otra acción.

## Verificación en la VM, 12 de noviembre de 2026

Después de instalar el paquete, `/health` respondió 200 y `/admin/status` mostró `intent_model_enabled=true` y las seis clases. El chat autorizado permaneció pausado. Se reintentó el mensaje antiguo que bloqueaba la cola; el trabajador marcó 526 entradas como procesadas y no quedaron entradas pendientes o fallidas ni se generaron nuevas salidas. La billetera consultada en Supabase siguió en S/ 7 y la última recarga aprobada continuó siendo la del 15 de octubre. Esto verifica arranque e integración técnica; la prueba de predicción y respuesta por WhatsApp con esta versión queda pendiente hasta que el dueño autorice reanudar el chat.
