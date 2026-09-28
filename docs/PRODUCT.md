# Vitalia (nombre de trabajo): Expediente de salud familiar con IA

> Documento de producto v0.1. Uso personal y familiar, no comercial. Autor: PM (Claude) con Eduardo como usuario y stakeholder único.

## 0. Decisiones tomadas (v0.2)

| Tema | Decisión |
|---|---|
| País y proveedores | México; estudios de Chopo y otros laboratorios privados (en su mayoría PDFs digitales) |
| Perfiles iniciales | Eduardo, Eugenia (esposa), Beatriz (mamá); perfiles adicionales ilimitados. Consentimiento explícito de cada adulto |
| Corpus inicial | ~30 documentos históricos, casi todos PDF y algunos DICOM; sirven de banco de pruebas |
| Plataformas | Web instalable (PWA) con cámara para móvil; app nativa queda para después |
| Hardware | MacBook Air M5, 16 GB: desarrollo y procesamiento local; no es servidor 24/7 |
| Privacidad de IA | **Híbrido (B):** OCR, anonimización y DICOM en local (modelos de ~4B a 7B cuantizados); a la API solo va texto anonimizado, con proveedor sin entrenamiento y retención mínima |
| Alojamiento MVP | Local-first en el Mac, cifrado (FileVault más cifrado de la app); acceso móvil por Tailscale; respaldo cifrado con llave propia |
| Evolución | Mini-PC o Mac mini dedicado, o VPS cifrado, cuando se necesite acceso 24/7 |
| Abstracción de modelos | **Agnóstico al modelo:** la interpretación médica pasa por una capa de proveedor intercambiable (varios proveedores de API y modelos locales). La elección se decide con un banco de pruebas propio (exactitud de extracción, calidad de explicación, alucinaciones) y no por reputación |
| Reglas clínicas | Rangos, banderas rojas y cálculos son deterministas y auditables; el modelo interpreta y explica, no decide |

Pendiente de confirmar por Eduardo: opción B y alojamiento local-first (recomendación del PM).

## 1. Visión

Un lugar privado donde toda la información médica de la familia (laboratorios, imágenes, recetas, expediente clínico, wearables) se convierte en una **línea de tiempo estructurada por persona**. Sobre ella, agentes de IA explican qué cambió, qué importa, qué preguntar al médico y qué falta por hacer.

**Frase del producto:** "Sube cualquier documento médico; entiende tu salud en el tiempo, con evidencia y sin perder la privacidad."

## 2. Problema

- Los resultados viven dispersos: PDFs de distintos laboratorios, fotos de recetas, CDs de imagen, portales de hospitales, correos.
- Cada laboratorio usa nombres, unidades y rangos distintos, así que las tendencias no se pueden comparar.
- Nadie ve la película completa: el médico ve una foto de la consulta.
- Las recetas y tratamientos se olvidan: qué se tomó, cuándo, para qué, con qué interacciones.
- Es un trabajo de una sola persona (tú) para 4 o más perfiles.

## 3. Referencia competitiva (qué copiar y qué no)

| Producto | Qué hace bien | Límite para nosotros |
|---|---|---|
| Function Health, Superpower, Mito Health | Panel amplio de biomarcadores, rangos "óptimos", plan de acción | Solo analizan **sus** laboratorios; nube de terceros; no unifican expediente previo |
| InsideTracker, Empirical Health | Edad biológica, recomendaciones de estilo de vida, integración con wearables | Igual: ecosistema cerrado, foco en optimización más que en expediente |
| Whoop Advanced Labs | Cruza laboratorios con datos fisiológicos continuos | Depende del hardware |
| Vitals Vault | Bóveda de registros propios | Poca inteligencia sobre los datos |

**Nuestra cuña:** ingerimos **cualquier** documento de **cualquier** origen, para **toda la familia**, con **privacidad total**, en español y con contexto local. Ellos venden análisis; nosotros tenemos el expediente completo.

## 4. Usuarios y perfiles

- **Admin (Eduardo):** sube, revisa, configura. Ve todo.
- **Miembro adulto:** ve solo su perfil, si lo desea.
- **Dependiente (hijos, adultos mayores):** perfil administrado por un adulto. Los rangos de referencia cambian por edad y sexo, y los pediátricos son distintos.
- Permisos por perfil y compartición temporal con un médico mediante un enlace de solo lectura con expiración.

## 5. Jobs to be done

1. "Guardé este estudio, no quiero perderlo ni volver a capturarlo."
2. "¿Cómo va mi colesterol, glucosa o ferritina en los últimos 5 años, aunque me los haya hecho en 3 laboratorios?"
3. "¿Qué cambió desde el último chequeo y debería preocuparme?"
4. "Voy a consulta: dame un resumen de una hoja con lo relevante y mis preguntas."
5. "¿Qué medicamentos toma mi mamá y hay interacciones?"
6. "¿Qué chequeos o vacunas me tocan por edad y antecedentes?"
7. "Explícame este reporte de resonancia en lenguaje sencillo."

## 6. Alcance funcional

### 6.1 Ingesta (P0)
- Subir PDF, foto de recetas y reportes, capturas de pantalla, y correos reenviados.
- Clasificación automática: laboratorio, imagen, receta, nota clínica, vacunas, otro.
- Extracción estructurada con **revisión humana** antes de confirmar (lado a lado: documento original y datos extraídos).
- Normalización a estándares: LOINC (analitos), UCUM (unidades), RxNorm o ATC (medicamentos), SNOMED o ICD-10 (diagnósticos).
- Conservar siempre el original y la trazabilidad de cada dato hacia su página y región del documento.

### 6.2 Línea de tiempo y biomarcadores (P0)
- Vista por persona: eventos, documentos, medicamentos, diagnósticos.
- Gráficas por biomarcador con rango de referencia del laboratorio **y** rango clínico por edad y sexo; el "rango óptimo" de longevidad se etiqueta aparte como opinión, no como norma.
- Detección de tendencias y cambios significativos (cambio de referencia más variabilidad biológica).
- Conversión de unidades transparente.

### 6.3 Medicamentos y tratamientos (P1)
- Lista activa por persona, con dosis, motivo, médico y fechas.
- Alertas de interacciones y duplicidades, con fuente citada.
- Recordatorios de resurtido y seguimiento.

### 6.4 Imagen (P1, luego P2)
- P1: analizar el **reporte** radiológico (texto), extraer hallazgos y seguimiento sugerido, y explicarlo.
- P2: visor DICOM y modelos de imagen especializados como *segunda lectura informativa*, nunca diagnóstico.

### 6.5 Asistente "Pregunta a tu expediente" (P0)
- Chat con recuperación sobre los datos de la persona elegida. **Toda respuesta cita el documento y el dato de origen.**
- Preguntas de ejemplo: "¿Cuándo fue mi última HbA1c?", "Compara mis lípidos 2021 contra hoy", "Prepara mi consulta con cardiología".

### 6.6 Preparación de consulta y resumen (P1)
- PDF de 1 a 2 páginas: problemas activos, medicamentos, últimos resultados relevantes, tendencias y preguntas sugeridas.

### 6.7 Prevención y seguimiento (P2)
- Chequeos, tamizajes y vacunas recomendadas según edad, sexo y antecedentes, con guías citadas.
- Antecedentes familiares que ajustan las recomendaciones.

### 6.8 Integraciones (P2)
- Apple Health, Google Health Connect, Whoop u Oura (exportaciones o APIs), FHIR y exportación completa (PDF y JSON).

### Fuera de alcance (v1)
Diagnóstico, prescripción, telemedicina, venta de suplementos, pedir estudios (no somos laboratorio), red social.

## 7. Arquitectura de IA

**Principio rector:** la IA **entiende y explica**; el código **determinista** guarda, convierte y calcula. Ningún número clínico llega a la base de datos sin validación (esquema, rangos plausibles, unidades) y, en el MVP, sin confirmación humana.

### Sistema multiagente (orquestador con agentes especializados)

| Agente | Responsabilidad | Modelo sugerido |
|---|---|---|
| **Orquestador** | Enruta tareas, mantiene el contexto de la persona | Modelo de razonamiento de frontera (Claude) |
| **Clasificador de documentos** | Tipo de documento, idioma, persona, fecha | Modelo rápido y barato |
| **Extractor** | Documento a JSON estructurado con evidencia por campo | Modelo multimodal (visión) más validación de esquema |
| **Normalizador** | Mapea a LOINC, UCUM, RxNorm, ICD-10 | Búsqueda en terminologías más LLM solo para casos ambiguos |
| **Verificador (crítico)** | Segunda pasada independiente sobre extracciones; marca discrepancias | Otro modelo o prompt distinto |
| **Analista clínico** | Tendencias, correlaciones, resumen del cambio | Modelo de frontera con datos ya estructurados |
| **Seguridad de medicamentos** | Interacciones, duplicidades, dosis | Base de conocimiento de fármacos más LLM para explicar |
| **Evidencia** | Busca guías y literatura (PubMed, guías) y cita | RAG sobre fuentes curadas |
| **Imagen (P2)** | Segunda lectura informativa sobre estudios | Modelo médico especializado (evaluar MedGemma u otros) |
| **Redactor de consulta** | Genera el resumen para el médico | Modelo de frontera |

**Modelos de salud especializados:** evaluar modelos abiertos médicos (por ejemplo la familia MedGemma) para ejecución **local** en imagen y texto sensible, contra modelos de frontera por API. Decidir con un banco de pruebas propio (ver §11), no por reputación.

### Salvaguardas de IA
- Citas obligatorias: sin fuente en el expediente o en la literatura, el sistema dice "no sé".
- Nunca diagnostica ni indica cambiar tratamiento; recomienda hablar con el médico.
- **Banderas rojas** (valores críticos, síntomas de urgencia) generan un aviso claro y prioritario.
- Evaluación continua: un conjunto de documentos reales anonimizados con respuestas correctas, para medir exactitud de extracción y regresiones al cambiar de modelo.

## 8. Privacidad y seguridad (requisito de primer nivel)

- Datos de salud = máxima sensibilidad. Cifrado en tránsito y en reposo, con cifrado a nivel de campo para identificadores.
- **Opciones de despliegue** (decisión pendiente, ver §12): (a) autoalojado en casa o VPS propio, (b) nube privada propia con región elegida.
- Proveedores de IA solo con **retención cero y sin entrenamiento** sobre nuestros datos; posibilidad de modo 100 % local con modelos abiertos.
- Minimizar lo que sale del entorno: eliminar o seudonimizar datos personales antes de enviarlos a una API cuando sea viable.
- Autenticación fuerte (passkeys), bitácora de accesos, respaldos cifrados y exportación total en todo momento.
- El repositorio de código es privado y **jamás** contiene datos de salud reales.

## 9. Principios de producto

1. Confianza antes que magia: todo dato muestra de dónde salió.
2. Humano en el ciclo hasta demostrar precisión.
3. Útil sin esfuerzo: subir un archivo debe bastar.
4. Familia primero: cambiar de persona es un clic.
5. Sin alarmismo: informar con contexto y prioridad.

## 10. Roadmap

| Fase | Contenido | Resultado |
|---|---|---|
| **0. Fundamentos** | Repo, modelo de datos, esquema de seguridad, banco de pruebas con 20 a 30 documentos propios | Decisiones técnicas validadas |
| **1. MVP (laboratorios)** | Perfiles, ingesta de PDF de laboratorio, revisión, línea de tiempo, gráficas, chat con citas | Reemplaza la carpeta de PDFs |
| **2. Expediente completo** | Recetas y medicamentos, notas clínicas, reportes de imagen, resumen de consulta | Un solo expediente |
| **3. Inteligencia** | Prevención, evidencia, interacciones, integración con wearables | Recomendaciones con fuentes |
| **4. Imagen avanzada** | Visor DICOM y modelos de imagen (segunda lectura) | Experimental |

## 11. Métricas de éxito

- **Exactitud de extracción** de valores de laboratorio ≥ 99 % tras el verificador; 100 % tras revisión humana.
- Tiempo de subida a datos confirmados < 2 min por documento.
- 100 % de las respuestas del asistente con al menos una cita verificable.
- Cero eventos de datos fuera del entorno acordado.
- Uso real: cada miembro activo sube y consulta al menos una vez por trimestre.

## 12. Riesgos y preguntas abiertas

**Riesgos:** alucinaciones o errores de OCR en cifras (mitigación: validación y revisión), falsa tranquilidad o alarma, fuga de datos, calidad variable de PDFs escaneados, rangos "óptimos" controvertidos.

**Preguntas para Eduardo:**
1. País y sistema de salud (define terminologías, laboratorios y normativa como LFPDPPP)?
2. ¿Quiénes son los perfiles iniciales (edades, condiciones conocidas)?
3. ¿Despliegue: autoalojado en casa, VPS propio o nube privada?
4. ¿Toleras que el contenido de los documentos se procese con una API de un proveedor de IA (con retención cero), o exiges 100 % local?
5. ¿Cuántos documentos históricos tienes y en qué formatos?
6. ¿Qué dispositivos y wearables usan?
7. ¿Solo web, o también app móvil para fotografiar recetas?
8. ¿Idioma: solo español, o también inglés para reportes de EE. UU.?

## 13. Aviso

Producto informativo para uso personal. **No es un dispositivo médico ni sustituye a un profesional de la salud.**
