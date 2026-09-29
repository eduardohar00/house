# House: plan técnico (Fase 0)

Estado: v0.1. Deriva de [PRODUCT.md](PRODUCT.md). Todo lo marcado **(por verificar)** es una hipótesis que la Fase 0 o 1 debe confirmar antes de depender de ella.

## 1. Restricciones que mandan el diseño
1. **Datos de salud en la Mac de Eduardo.** Local-first, cifrado. Nada de datos reales en el repositorio ni en la sesión de desarrollo.
2. **Un solo modo de IA: híbrido.** Lo que puede hacerse en la Mac (leer, limpiar datos personales, DICOM) se hace ahí. A la IA solo llega texto sin datos personales.
3. **Agnóstico al modelo.** El proveedor se elige por tarea y por configuración. La elección se decide con el banco de pruebas, no por reputación.
4. **La IA propone, el código dispone.** Conversión de unidades, rangos, estados y tendencias son deterministas. Ninguna fila entra sin evidencia literal en el documento y sin confirmación humana (al inicio, siempre).
5. **Una sola máquina en el MVP.** Tres personas usan la app en la laptop de Eduardo, cada una con su PIN. Acceso desde otros equipos queda para una fase posterior.
6. **Presupuesto de IA acotado** (tope mensual configurable).

## 2. Arquitectura

```
                 ┌──────────────────────── Mac de Eduardo ────────────────────────┐
 Navegador  ───▶ │  Web app (React)  ◀──▶  API local (FastAPI, 127.0.0.1)          │
 (localhost)     │                          │                                      │
                 │   ┌──────────────────────┼─────────────────────────────┐        │
                 │   │ Ingesta: PDF/foto/DICOM → texto (local) → limpieza  │        │
                 │   │ de datos personales (local) → vista "lo que ve la IA"│       │
                 │   └──────────────────────┬─────────────────────────────┘        │
                 │                          ▼                                      │
                 │   Router de proveedores ──▶ (solo texto anonimizado) ──▶ API de IA
                 │                          │                                      │
                 │   Verificación determinista → revisión humana → SQLite cifrado  │
                 │   Originales cifrados en disco · Bitácora de IA (sin contenido) │
                 └─────────────────────────────────────────────────────────────────┘
```

### 2.1 Componentes y herramientas
| Capa | Elección | Estado |
|---|---|---|
| Lenguaje del núcleo | Python 3.11+ | Fase 0 hecha |
| API local | FastAPI en `127.0.0.1` | Fase 1 |
| Frontend | **Decidido en Fase 1:** HTML + JavaScript sin paso de compilación, servido por la propia API (la Mac no tiene Node y no hace falta). Parte de la maqueta en `docs/mockup/`. PWA después | Fase 1 |
| Base de datos | **Decidido en Fase 1:** SQLite en `~/Library/Application Support/House` (fuera del repo, permisos 700/600) sobre disco con FileVault (verificado activo). SQLCipher queda como mejora si se sale de la Mac | `src/house/app/store.py` |
| Texto de PDFs digitales | pdfplumber | Fase 0 (opcional) |
| OCR local | Apple Vision **(por verificar** el enlace desde Python**)** o PaddleOCR | Fase 1 |
| Limpieza de datos personales | Reglas propias (hecho) + Presidio como segunda capa | Reglas: Fase 0. Presidio: Fase 1 |
| Modelo de visión local para escaneos difíciles | Modelo pequeño (4B a 7B, cuantizado) con MLX **(por verificar** calidad en español**)** | Fase 1 |
| DICOM | pydicom (limpieza de metadatos) + OHIF para el visor | Fase 2 |
| Búsqueda semántica del asistente | Consultas SQL como herramientas del agente primero; embeddings locales después si hacen falta | Fase 2 |
| Cifrado de originales | AES-256-GCM por archivo; llave maestra en el llavero de macOS (no depende del PIN del admin, así cada persona abre sus documentos). Llave de recuperación: pendiente con el respaldo | `src/house/app/vault.py` |

### 2.2 Capa de proveedores (implementada)
- `Provider.complete_json(LLMRequest) -> LLMResponse`: salida estructurada con esquema JSON estricto.
- Adaptadores: **Anthropic** (escrito según la documentación vigente del SDK; probado con un cliente simulado, **no contra la API real**), **OpenAI** y **Gemini** (escritos sin verificar; requieren prueba real y un ID de modelo vigente).
- `Router`: tarea → proveedor según `config/house.toml`; aplica el **tope mensual** leyendo una bitácora JSONL que guarda tokens y costo, nunca contenido.
- Errores tipados (`ProviderError`, `ProviderRefusal`, `BudgetExceeded`) sin incluir el prompt en el mensaje.
- Fase 1 añade `chat_with_tools` para el asistente (bucle de herramientas sobre la base de datos del perfil abierto).
- Defaults documentados: `claude-opus-5-5` con `effort: medium`. Se cambia por configuración; el banco de pruebas dice si un modelo más barato basta.

### 2.3 Pipeline de extracción (implementado)
1. Texto del documento (pdfplumber o OCR local).
2. `Anonymizer.scrub`: CURP, RFC, correo, teléfono, folios, domicilio, encabezados de paciente y médico, nombres conocidos; la fecha de nacimiento se reemplaza por la edad. Devuelve qué se quitó, para la vista "Lo que ve la IA".
3. `Router.complete_json(task="extract")` con esquema estricto.
4. **Verificación determinista** por fila: evidencia literal presente en el texto enviado y valor dentro de la evidencia; analito reconocido (LOINC semilla); unidad convertible; valor plausible. Todo problema marca la fila "a revisar".
5. Normalización a unidad canónica (la conversión se marca siempre para revisión), lectura del rango impreso y estado (`low/ok/high`; `abnormal` para resultados de texto). Se guardan también la sección del estudio y el método impreso; `normalize/series.mixed_methods` avisa cuando una serie mezcla métodos.
6. Revisión humana lado a lado. Solo lo confirmado se escribe en `observation`.

### 2.4 Asistente (Fase 2)
Agente con herramientas de solo lectura sobre el perfil abierto (`get_series`, `list_documents`, `get_problems`, `get_medications`, …). Las respuestas deben citar documento y dato; sin cita, el asistente dice que no lo sabe. No hay herramientas de escritura. Rechaza diagnosticar. El **verificador** es una segunda llamada, idealmente de otro proveedor, que comprueba que cada afirmación tiene respaldo.

### 2.5 Multiagente: solo donde ayuda
No se orquestan agentes por moda. El flujo es una tubería con pasos deterministas y llamadas puntuales. Se justifica un agente separado en: (a) el **verificador** independiente, (b) el asistente con herramientas, (c) la **segunda lectura de imagen** (experimental, desactivada por defecto). El resto no lo necesita.

### 2.6 Fase 1: orden de construcción
1. **Base (hecho):** `python -m house.app` en 127.0.0.1:8765; primer uso crea al admin; perfiles con PIN (scrypt), bloqueo tras 5 intentos y por 15 min de inactividad; cada persona ve solo su perfil; accesos del admin a perfiles ajenos en `access_log`; rechazo de otros Host y de escrituras sin cabecera `X-House`.
2. **Subir estudio (servidor hecho; pantallas en la parte 3):** `POST /api/people/{id}/documents` → texto (pdfplumber) → limpieza → Claude → verificación → `extraction_row`; `GET /api/documents/{id}` (filas + "lo que vio la IA"), `GET …/file` (original descifrado), `POST …/review` (solo lo aceptado pasa a `observation`). Duplicados por SHA-256; escaneos rechazados hasta tener OCR. `person_alias` guarda cómo aparece el nombre en los estudios para quitarlo antes de enviar.
3. Resumen y gráficas según la maqueta (incluye aviso de métodos mezclados).
4. Expediente clínico manual.

## 3. Seguridad y privacidad
| Tema | Decisión |
|---|---|
| Acceso | PIN (hash con sal, nunca en claro) y passkey opcional; bloqueo por inactividad; el admin restablece PIN |
| Roles | Cada persona ve solo su perfil; el admin ve todos; el acceso del admin a un perfil ajeno queda en `access_log` |
| Red | API solo en `127.0.0.1`; sin acceso remoto en el MVP |
| Datos en reposo | Base de datos y originales cifrados; respaldo cifrado con llave de recuperación que solo Eduardo guarda |
| Datos hacia la IA | Solo texto anonimizado; vista previa; proveedores con no entrenamiento y retención mínima **(verificar términos vigentes de cada uno)**; bitácora sin contenido |
| Errores | Los mensajes de error nunca incluyen contenido del documento (probado) |
| Repositorio | Sin datos reales. CI falla si aparecen PDF, DICOM o carpetas de datos |
| Borrado | Borrar un perfil elimina en cascada sus datos y originales (probado a nivel de esquema) |

Límite conocido: el limpiador por reglas puede dejar pasar un nombre que no esté en la lista ni en un encabezado reconocible. Mitigaciones: lista de nombres de la familia, Presidio en Fase 1 y vista previa obligatoria las primeras semanas.

## 4. Banco de pruebas (implementado)
`python -m house.bench` compara proveedores sobre los mismos casos (ver `bench/README.md`). Mide exactitud, faltantes, filas extra, filas sin respaldo, carga de revisión, fugas de datos personales, costo y latencia. Incluye una **línea base sin IA** (regex) para saber cuánto aporta un modelo. Sobre el caso sintético limpio, la línea base acierta 9 de 9 (y Claude también); la diferencia entre una regla y un modelo debe aparecer con documentos reales y desordenados. Los PDF colapsan los espacios entre columnas al extraer su texto, y la línea base ya lo tolera.

**Lo que falta y solo puede hacer Eduardo:** armar 30 casos con `expected.json` a mano a partir de sus PDFs reales, en `bench/private/`, y correr el banco en su Mac con sus claves.

## 5. Plan por fases
| Fase | Alcance | Criterio de salida |
|---|---|---|
| **0 (esta)** | Plan, esquema de datos, capa de proveedores, anonimizador, normalización, pipeline y banco de pruebas | 30 pruebas pasan; el banco corre offline; documentación |
| **0b (Eduardo)** | Casos reales y primer reporte del banco con 2 o 3 proveedores | Decidir proveedor por tarea con datos |
| **1 MVP laboratorios** | API local, base cifrada, PIN, ingesta de PDF, revisión lado a lado, resumen y gráficas, expediente manual | Un flujo completo con documentos reales de Eduardo |
| **2 Expediente e inteligencia** | Asistente con citas, medicamentos, reportes de imagen, DICOM básico | Precisión del asistente medida con preguntas reales |
| **3 Familia y móvil** | Acceso desde otros equipos (Tailscale o equipo dedicado), PWA con cámara, notificaciones | Eugenia y Beatriz lo usan solas |

## 6. Riesgos
| Riesgo | Mitigación |
|---|---|
| El modelo se equivoca en una cifra | Evidencia literal obligatoria, revisión humana al inicio, banco de pruebas continuo |
| Fuga de datos personales al proveedor | Limpieza + vista previa + prueba de fuga en el banco (debe ser 0) |
| Adaptadores de OpenAI y Gemini mal escritos | Marcados sin verificar; prueba real en Fase 0b |
| Rangos "óptimos" controvertidos | Solo rangos de referencia del laboratorio y clínicos; lo demás, etiquetado como opinión |
| Perder el Mac | Respaldo cifrado automático y llave de recuperación |
| Costo de IA | Tope mensual, bitácora de gasto, modelo más barato si el banco lo permite |
| Umbrales clínicos del resumen (Atención, Vigilar) | Revisión por un médico antes de fijarlos |

## 7. Decisiones abiertas para Eduardo
1. Qué proveedores comparar en el banco (mínimo dos).
2. Si el cifrado de la base va con SQLCipher o con volumen cifrado (decisión técnica que puedo proponer tras un experimento corto en Fase 1).
3. Quién puede revisar clínicamente los umbrales de "Vigilar" y las explicaciones de cada marcador.
