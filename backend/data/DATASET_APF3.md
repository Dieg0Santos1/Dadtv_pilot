# Datos de clasificación APF3

`training_data.csv` está versionado en esta carpeta. Contiene 180 frases cortas
con las columnas `text,label`; no contiene teléfonos, nombres, credenciales ni
comprobantes. Las etiquetas se asignaron manualmente. El archivo y el código
disponible no registran la fuente primaria de cada frase, ni incluyen IDs de
conversaciones o consentimiento para reutilizarlas. Por tanto, **no se puede
afirmar que provengan de conversaciones reales**. Para el informe deben tratarse
como ejemplos manuales/controlados de procedencia no verificada, nunca como una
muestra representativa de clientes. No se puede separar con evidencia una
proporción de reales y simulados.

| Clase | Entrenamiento | Prueba controlada |
|---|---:|---:|
| `consulta` | 49 | 6 |
| `pedido` | 43 | 6 |
| `reclamo` | 22 | 6 |
| `fuera_de_tema` | 20 | 6 |
| `ofensivo` | 20 | 6 |
| `spam` | 26 | 6 |
| **Total** | **180** | **36** |

`apf3_final_test.csv` contiene 36 frases nuevas redactadas como prueba
controlada para APF3. No son mensajes de usuarios. Se fijó el script, sus
candidatos, la regla de selección y el tamaño de entrenamiento antes de
evaluar esta versión de la prueba. La partición antigua de 36 frases del
entrenamiento ya se había inspeccionado durante APF2: está excluida de la
selección y del modelo exportado, y no se presenta como evaluación final.

La prueba controlada nueva aporta una medición interna reproducible. Sigue
siendo pequeña, simulada y redactada por el equipo; para medir el rendimiento
con clientes reales hace falta un conjunto externo con procedencia y revisión
humana documentadas.
