# Comparación de modelos APF3

El script `backend/train_apf3_classifier.py` usa las mismas 108 frases de
entrenamiento y 36 de validación para los tres candidatos. Las 36 frases de
la antigua partición APF2 se excluyen de esta comparación porque ya habían
sido inspeccionadas. La selección usa macro-F1 de validación; accuracy y
nombre resuelven empates. Las clases y el orden de filas/columnas son:
`consulta`, `fuera_de_tema`, `ofensivo`, `pedido`, `reclamo`, `spam`.

| Modelo | Configuración | Aciertos/36 | Accuracy | Macro-F1 |
|---|---|---:|---:|---:|
| Línea base | TF-IDF unigramas + clase mayoritaria | 10 | 0,278 | 0,072 |
| Regresión logística | TF-IDF unigramas; `C=4`, pesos balanceados | **30** | **0,833** | **0,837** |
| SVM lineal | TF-IDF unigramas; `C=1`, pesos balanceados | 29 | 0,806 | 0,807 |

Matriz de confusión de la **línea base** (filas: clase real; columnas: clase
predicha, en el orden indicado):

```text
[[10, 0, 0, 0, 0, 0],
 [ 4, 0, 0, 0, 0, 0],
 [ 4, 0, 0, 0, 0, 0],
 [ 8, 0, 0, 0, 0, 0],
 [ 5, 0, 0, 0, 0, 0],
 [ 5, 0, 0, 0, 0, 0]]
```

Matriz de confusión de **regresión logística**:

```text
[[8, 0, 0, 1, 0, 1],
 [0, 3, 0, 1, 0, 0],
 [1, 0, 3, 0, 0, 0],
 [0, 0, 0, 8, 0, 0],
 [2, 0, 0, 0, 3, 0],
 [0, 0, 0, 0, 0, 5]]
```

Matriz de confusión de **SVM lineal**:

```text
[[9, 0, 0, 0, 1, 0],
 [0, 3, 0, 1, 0, 0],
 [1, 0, 3, 0, 0, 0],
 [1, 0, 0, 7, 0, 0],
 [3, 0, 0, 0, 2, 0],
 [0, 0, 0, 0, 0, 5]]
```

La regresión logística ganó y se ajustó con las 144 frases de desarrollo.
El JSON exportado conserva SHA256
`E160A5ADC3DFB3C7BE65759CED6287D3A6817BF8E5F961916EEB22050D79297F`,
igual al instalado y probado por WhatsApp en la VM. La prueba separada de
36 frases simuladas dio 25/36 aciertos y macro-F1 0,695, pero **ya se había
consultado antes de añadir SVM**: es un resultado descriptivo, no una nueva
evaluación independiente. Tampoco mide el rendimiento con clientes reales.

Las métricas por clase, matrices completas, hashes y configuración están en
`backend/bot/model/intent_apf3_report.json`. El CSV de entrenamiento es
`backend/data/training_data.csv`; `backend/data/DATASET_APF3.md` documenta
las limitaciones de procedencia y `backend/data/apf3_class_counts_origin.csv`
permite importar sus conteos. El entrenamiento requiere
`backend/requirements-training.txt` y se reproduce con:

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe backend\train_apf3_classifier.py
```

La demostración del modelo final dentro del bot está en
`APF3_PRUEBA_WHATSAPP_2026-11-16.md` y su log filtrado en
`evidence/apf3-ml-log-2026-11-16.txt`. Las capturas aún requieren ocultar
el nombre y la foto del contacto antes de adjuntarlas al informe.
