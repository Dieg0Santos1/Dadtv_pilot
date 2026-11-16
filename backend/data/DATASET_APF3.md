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

El mismo conteo y estado de procedencia está disponible en
`apf3_class_counts_origin.csv` para importarlo al informe. `not_documented`
significa que no se conservó la fuente primaria; `unverified` significa
que no es posible afirmar que la frase venga de una conversación real.

`apf3_final_test.csv` contiene 36 frases redactadas como prueba controlada
para APF3. No son mensajes de usuarios. Se evaluó antes de agregar SVM a la
comparación, por lo que su resultado se reutiliza solo como descripción y
no como una evaluación independiente nueva. La partición antigua de 36
frases del entrenamiento ya se había inspeccionado durante APF2: está
excluida de la selección y del modelo exportado.

La prueba controlada nueva aporta una medición interna reproducible. Sigue
siendo pequeña, simulada y redactada por el equipo; para medir el rendimiento
con clientes reales hace falta un conjunto externo con procedencia y revisión
humana documentadas.
