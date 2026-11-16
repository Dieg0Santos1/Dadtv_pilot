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

El modelo seleccionado se instaló en la VM el 16 de noviembre de 2026. El
instalador verificó SHA256, `/health`, las seis clases y que el chat de prueba
seguía pausado. Después, con autorización del dueño, se reanudó únicamente
ese contacto y se hizo una prueba controlada. La conversación y las líneas
reales de `intent_ml`/`route_ml` se analizan en
[APF3_PRUEBA_WHATSAPP_2026-11-16.md](APF3_PRUEBA_WHATSAPP_2026-11-16.md).
Las capturas aportadas muestran datos de perfil y aún requieren ocultarlos
antes de anexarse al informe o publicarse.
