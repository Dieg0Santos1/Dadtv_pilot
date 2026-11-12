# Clasificador APF3 integrado al bot

El bot ejecuta un clasificador entrenado para `consulta`, `pedido`, `reclamo`,
`fuera_de_tema`, `ofensivo` y `spam`. `backend/bot/intent_model.py` calcula la
intención, confianza y margen dentro del flujo de mensajes. El servicio registra
`intent_ml`, `confidence`, `margin` y `route_ml`; OpenAI puede redactar respuestas,
pero su salida no reemplaza la predicción del clasificador.

`pedido` conduce al catálogo/cotización; `consulta` a información; `reclamo`
pausa el chat y lo deriva a una persona; las otras tres clases reciben una
respuesta breve. Si la confianza es menor que 0,45, el margen menor que 0,15
o no hay términos conocidos, el bot pide aclaración. Una afirmación como
«ya pagué» o una imagen no acredita dinero: la recarga requiere verificación
del comprobante y aprobación de la RPC correspondiente.

## Datos y comparación reproducible

El CSV público está en `backend/data/training_data.csv`. Su origen y conteo
por clase están documentados en [DATASET_APF3.md](backend/data/DATASET_APF3.md).
No hay trazabilidad que permita afirmar que sean mensajes reales de clientes.

`backend/train_apf3_classifier.py` fija primero una partición de 36 frases
antiguas ya inspeccionadas, que se excluye de la selección y del modelo
exportado. Divide las 144 restantes en 108 de entrenamiento y 36 de
validación. Compara tres candidatos sobre **la misma validación** y elige por
macro-F1, con accuracy y nombre como desempate:

| Modelo | Accuracy de validación | Macro-F1 de validación |
|---|---:|---:|
| Línea base: clase mayoritaria | 27,8 % | 0,072 |
| TF-IDF unigramas + regresión logística | 83,3 % | **0,837** |
| TF-IDF unigramas/bigramas + regresión logística | 72,2 % | 0,715 |

La configuración elegida es TF-IDF de unigramas con normalización Unicode,
`sublinear_tf=True`, más `LogisticRegression(C=4, class_weight="balanced",
max_iter=2000)`. Tras seleccionarla, se ajustó con las 144 frases de
desarrollo. Se evaluó una vez en `backend/data/apf3_final_test.csv`, un conjunto
separado de **36 frases simuladas** (seis por clase): 25 aciertos, accuracy
69,4 % y macro-F1 0,695. La matriz de confusión, las métricas por clase,
hashes y configuración completa están en
`backend/bot/model/intent_apf3_report.json`. El orden de filas y columnas
figura en `selection.classes_order`.

La antigua cifra de 72,2 % sobre la partición APF2 no es una evaluación final
independiente: esa partición se consultó al comparar configuraciones. La nueva
prueba es controlada, pequeña y simulada; tampoco representa a clientes reales.
Las confianzas del modelo no están calibradas como probabilidades de acierto.

El modelo elegido se exporta como JSON en
`backend/bot/model/intent_apf3.json`; la VM no necesita scikit-learn ni carga
un pickle. Para reproducir la comparación y exportación:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-training.txt
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe backend\train_apf3_classifier.py
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

## Estado de la demostración por WhatsApp

La versión anterior del clasificador quedó instalada en la VM el 12 de
noviembre de 2026: `/health` devolvió 200 y `/admin/status` informó las seis
clases. El chat de prueba permaneció pausado. **El modelo seleccionado en este
informe es una nueva exportación local y todavía no se ha desplegado en la VM.**
Tampoco existe aún una captura de WhatsApp ni un log de esta versión que
relacione intención, confianza y ruta. Para obtener esa evidencia hace falta
que el dueño autorice reanudar el número de prueba, desplegar esta versión y
hacer una conversación controlada. Hasta entonces, no debe afirmarse que esta
versión se demostró por WhatsApp.
