# House: Expediente de salud familiar con IA

> Documento de producto v0.1. Uso personal y familiar, no comercial. Autor: PM (Claude) con Eduardo como usuario y stakeholder único.

## 0. Decisiones tomadas (v0.2)

| Tema | Decisión |
|---|---|
| País y proveedores | México; estudios de Chopo y otros laboratorios privados (en su mayoría PDFs digitales) |
| Perfiles iniciales | Eduardo, Eugenia (esposa), Beatriz (mamá); perfiles adicionales ilimitados. Consentimiento explícito de cada adulto |
| Usuarios y permisos | Eduardo, Eugenia y Beatriz usan la app con cuenta propia. Cada una ve solo su perfil; solo Eduardo (admin) administra y ve todos. Las dos aceptan de forma explícita que el admin vea sus datos, y pueden exportar o pedir borrar su perfil |
| Nombre | House (nombre definitivo del producto y del repo) |
| Corpus inicial | ~30 documentos históricos, casi todos PDF y algunos DICOM; sirven de banco de pruebas |
| Plataformas | MVP: aplicación web local en la laptop. Después: acceso desde otros equipos y PWA móvil con cámara para recetas; app nativa más adelante |
| Hardware | MacBook Air M5, 16 GB: desarrollo y procesamiento local; no es servidor 24/7 |
| Privacidad de IA | **Híbrido (B):** OCR, anonimización y DICOM en local (modelos de ~4B a 7B cuantizados); a la API solo va texto anonimizado, con proveedor sin entrenamiento y retención mínima |
| Alojamiento MVP | Local-first en el Mac, cifrado (FileVault más cifrado de la app); acceso móvil por Tailscale; respaldo cifrado con llave propia |
| Evolución | Mini-PC o Mac mini dedicado, o VPS cifrado, cuando se necesite acceso 24/7 |
| Abstracción de modelos | **Agnóstico al modelo:** la interpretación médica pasa por una capa de proveedor intercambiable (varios proveedores de API y modelos locales). La elección se decide con un banco de pruebas propio (exactitud de extracción, calidad de explicación, alucinaciones) y no por reputación |
| Reglas clínicas | Rangos, banderas rojas y cálculos son deterministas y auditables; el modelo interpreta y explica, no decide |

| Confirmado | Arquitectura híbrida (B) y local-first |
| Revisión humana | Obligatoria al inicio; se relaja solo a lo marcado cuando la precisión lo justifique |
| Alcance | Histórico, insights y tips; sin venta de paneles ni planes de optimización |
| Acceso (confirmado) | **MVP: una sola máquina, la laptop de Eduardo.** Eugenia y Beatriz usan la app en esa laptop, cada una con su propio acceso (PIN o passkey) y viendo solo su perfil. Acceso desde sus propios equipos y celulares queda para una fase posterior (Tailscale, equipo dedicado o VPS cifrado). Antes: Siguiente paso: equipo dedicado en casa, o VPS cifrado si se necesita 24/7 |

## 0.1 Diseño de la experiencia (v0.3, maqueta aprobada)

Maqueta interactiva con datos ficticios: [`docs/mockup/house-mockup.html`](mockup/house-mockup.html) (se abre en el navegador). Es dirección de diseño, no código de producción.

**Estructura de la app:** selector de perfil arriba (con PIN o passkey para cambiar de persona) y siete secciones: **Resumen**, **Expediente clínico**, **Documentos**, **Imagen**, **Asistente**, **Familia** y **Configuración** (las dos últimas solo para el admin).

**Resumen**
- Los marcadores se agrupan por sistema, en este orden: metabolismo de la glucosa, lípidos y riesgo cardiovascular, hígado, riñón, sangre y hierro, tiroides, vitaminas. Cada grupo muestra cuántos de sus marcadores están en rango.
- Cada marcador es una tarjeta con valor, estado (con icono y texto, nunca solo color), tendencia y mini-gráfica.
- Al pasar el cursor sobre una tarjeta aparece una explicación breve de qué mide el marcador y el último resultado de la persona. En celular la explicación está bajo el título de la gráfica.
- Al elegir un marcador se ve su evolución completa con el rango de referencia, tooltip por punto (fecha, valor, estado, laboratorio) y vista de tabla.
- **Resumen priorizado del estudio**, con tres niveles: *Atención* (fuera de rango, ordenado por distancia al rango, con tendencia), *Vigilar* (en rango pero acercándose al límite) y *Mejoró* (entró al rango). Cierra con una línea de lo estable.
- Los rangos dependen de sexo (y, a futuro, de edad). Los umbrales de "Vigilar" y de tendencia son provisionales y **deben revisarse con un médico**.

**Expediente clínico:** alergias, problemas de salud con estado, medicamentos actuales, antecedentes familiares, cirugías y un historial cronológico unificado (consultas, laboratorio, imagen, vacunas, cirugías) con filtros por tipo.

**Documentos:** tabla de originales con tipo, origen y estado de revisión de la extracción; punto de entrada para agregar documentos.

**Subir y revisar un documento**
- Flujo: elegir archivo, procesamiento visible por pasos (lectura y limpieza en la Mac, extracción y verificación con IA), revisión y guardado.
- Revisión lado a lado: el documento original con la fila resaltada y la tabla de datos extraídos (nombre en el documento, nombre normalizado con LOINC, valor editable, unidad, referencia, barra de confianza).
- Lo dudoso se marca con la razón (lectura de OCR dudosa, método distinto al histórico, conversión de unidades) y **hay que confirmarlo o corregirlo** antes de poder guardar.
- Pestaña "Lo que ve la IA": el texto exacto que se enviaría, con nombre, folio y médico reemplazados.
- Salvaguarda de persona: si el documento parece de otra persona que el perfil abierto, no se puede guardar; solo el admin puede asignarlo.

**Asistente ("Pregunta a tu expediente")**
- Respuestas solo con datos del perfil abierto, con citas al documento de origen (laboratorio y fecha) y tablas cuando ayudan.
- Ante "¿Tengo diabetes?" no diagnostica: muestra los datos y remite al médico.
- Ante un dato que no existe (presión arterial de 2023) responde que no lo encontró y qué documento ayudaría; no inventa.
- Preguntas sugeridas como punto de partida.

**Familia (solo admin):** tarjeta por persona (atención, vigilar, mejoraron), pendientes de toda la familia (resurtidos, prevención, citas), y panel de datos y privacidad (último respaldo, documentos enviados a la IA, uso mensual de IA frente al tope).

**Primer uso:** asistente de 6 pasos: bienvenida, PIN (con passkey opcional), perfil propio (fecha de nacimiento y sexo, que determinan los rangos), familia (parentesco y tipo de acceso: cuenta propia o solo administrado por Eduardo), IA y privacidad (híbrido o solo local, proveedor y tope mensual) y resumen final con acceso directo a subir el primer documento. El paso de familia se reutiliza para "Agregar perfil".

**Alta manual en el expediente:** botón "Agregar" en alergias, problemas de salud, medicamentos (con sugerencias de catálogo), antecedentes familiares y cirugías.

**Imagen:** lista de estudios por persona y visor con brillo, contraste, zoom y corte (imágenes ilustrativas en la maqueta), reporte del radiólogo, hallazgos extraídos, seguimiento indicado y explicación en palabras sencillas. Los metadatos personales del DICOM se eliminan en local. La segunda lectura con IA es experimental y está desactivada por defecto. Desde el historial cronológico, cada estudio de imagen enlaza a su visor.

**Errores y estados:** archivo ilegible (reintentar con lectura reforzada, capturar a mano), sin conexión con el proveedor de IA (reintentar, modelo local, cola), valor crítico (aviso rojo con lenguaje sin diagnóstico), perfil nuevo sin datos, documento duplicado, respaldo atrasado, modelo local faltante y tope de gasto alcanzado.

**Configuración (solo admin):** modo de privacidad, **proveedor de IA elegible por tarea** (extracción, interpretación y chat, verificación), tope mensual y uso, activación de la segunda lectura de imágenes, modelos instalados en la Mac, respaldo (destino, frecuencia, clave de recuperación), seguridad (bloqueo por inactividad, passkeys) y exportación total.

**Pendiente de diseñar:** recordatorios y notificaciones y versión móvil con cámara (ambos para fases posteriores).

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

- **Admin (Eduardo):** el único que ve todos los perfiles. Sube, revisa y configura.
- **Miembro adulto (Eugenia, Beatriz):** usa la app con su propia cuenta y ve solo su propio perfil; nunca ve los de otros. El admin puede ver el suyo, con su consentimiento explícito.
- **Dependiente (hijos, adultos mayores):** perfil administrado por un adulto. Los rangos de referencia cambian por edad y sexo, y los pediátricos son distintos.
- Permisos por perfil. Compartir con un médico queda fuera de alcance por ahora.

## 5. Jobs to be done

1. "Guardé este estudio, no quiero perderlo ni volver a capturarlo."
2. "¿Cómo va mi colesterol, glucosa o ferritina en los últimos 5 años, aunque me los haya hecho en 3 laboratorios?"
3. "¿Qué cambió desde el último chequeo y debería preocuparme?"
4. "¿Qué medicamentos toma mi mamá y hay interacciones?"
5. "¿Qué chequeos o vacunas me tocan por edad y antecedentes?"
6. "Explícame este reporte de resonancia en lenguaje sencillo."

## 6. Alcance funcional

### 6.0 Expediente clínico por persona (P0)
- Historial completo de cada perfil en un solo lugar: antecedentes personales y familiares, alergias, diagnósticos, consultas y notas, cirugías, hospitalizaciones, vacunas y medicamentos actuales y pasados.
- Todos los documentos originales en orden cronológico, vinculados a los datos estructurados que se extrajeron de ellos.
- Alcance del producto: **histórico, insights y tips**. No se venden paneles de laboratorio ni planes de optimización.

### 6.1 Ingesta (P0)
- Subir PDF, foto de recetas y reportes, capturas de pantalla, y correos reenviados.
- Clasificación automática: laboratorio, imagen, receta, nota clínica, vacunas, otro.
- Extracción estructurada con **revisión humana** antes de confirmar (lado a lado: documento original y datos extraídos).
- Normalización a estándares: LOINC (analitos), UCUM (unidades), RxNorm o ATC (medicamentos), SNOMED o ICD-10 (diagnósticos).
- Conservar siempre el original y la trazabilidad de cada dato hacia su página y región del documento.

### 6.2 Línea de tiempo y biomarcadores (P0)
- Vista por persona: eventos, documentos, medicamentos, diagnósticos.
- Selector de biomarcadores: elegir cualquiera (colesterol, glucosa, ferritina, etc.) y ver su evolución completa, aunque provenga de laboratorios distintos.
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
- Preguntas de ejemplo: "¿Cuándo fue mi última HbA1c?", "Compara mis lípidos 2021 contra hoy".

### 6.7 Prevención y seguimiento (P2)
- Chequeos, tamizajes y vacunas recomendadas según edad, sexo y antecedentes, con guías citadas.
- Antecedentes familiares que ajustan las recomendaciones.

### 6.8 Integraciones (P2)
- Apple Health, Google Health Connect, Whoop u Oura (exportaciones o APIs), FHIR y exportación completa (PDF y JSON).

### Fuera de alcance (v1)
Resumen o preparación de consulta para el médico y compartir el expediente con terceros (decisión de Eduardo).
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
| **2. Expediente completo** | Recetas y medicamentos, notas clínicas, reportes de imagen | Un solo expediente |
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
