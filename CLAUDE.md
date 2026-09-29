# House: contexto para Claude Code

House es un expediente de salud **familiar y privado** (uso personal de Eduardo, Eugenia y Beatriz). Convierte estudios de laboratorio, imágenes, recetas y expediente clínico en una línea de tiempo por persona. Responde siempre en **español** al usuario (Eduardo).

Documentos clave (léelos antes de proponer cambios grandes):
- `docs/PRODUCT.md`: producto, decisiones y alcance.
- `docs/TECHNICAL_PLAN.md`: arquitectura, seguridad, fases y riesgos.
- `docs/mockup/house-mockup.html`: maqueta interactiva aprobada (datos ficticios).
- `bench/README.md`: banco de pruebas de extracción.

## Reglas de privacidad (obligatorias)
1. Eduardo autoriza que Claude Code lea `bench/private/` y sus PDFs de estudios (datos de salud reales) para ayudar a armar y revisar casos del banco. Aun así, `verified: true` en `expected.json` se pone solo cuando Eduardo confirma los valores contra su PDF (Claude hace el cambio): la respuesta correcta del banco no la fija un modelo que el banco evalúa. No leas `config/house.toml` (claves).
2. Nunca subas a git datos de salud, claves de API ni `config/house.toml`. La CI falla si hay PDF o DICOM en el repo.
3. Los datos de prueba del repo son **sintéticos**. Para nuevas pruebas usa datos inventados.
4. Los mensajes de error y las bitácoras no deben incluir contenido de documentos.

## Decisiones de producto ya tomadas
- Modo de IA **único: híbrido**. Lectura, limpieza de datos personales y DICOM ocurren en la Mac; a la IA solo llega texto anonimizado.
- **Agnóstico al modelo**: el proveedor se elige por tarea en `config/house.toml`. **Decisión vigente (2026-09-28): todo lo hace Claude** (extracción, interpretación y verificación); no comparar otros proveedores por ahora. La capa de proveedores se conserva para poder cambiar después. El banco se usa para medir a Claude contra la línea base.
- **La IA propone, el código dispone**: conversiones, rangos, estados y tendencias son deterministas; toda fila requiere evidencia literal y revisión humana (al inicio, siempre).
- MVP en **una sola máquina** (laptop de Eduardo). Onboarding **solo lo hace Eduardo** (admin): crea perfiles y asigna PIN. Cada persona ve solo su perfil.
- Fuera de alcance: diagnóstico, resumen o preparación de consulta, compartir con terceros, vender paneles de laboratorio.
- Los umbrales de "Atención/Vigilar" y las explicaciones de marcadores son provisionales y **deben revisarse con un médico**.

## Cómo trabajar
```bash
source .venv/bin/activate
pytest -q
ruff check . && ruff format --check .
python -m house.bench                       # banco offline
python -m house.bench.new_case <pdf> --id <id> --names "Nombre"   # crea un caso en bench/private/
python -m house.bench.names bench/private/<id>                    # solo nombres y unidades sin reconocer
python -m house.app.restore <archivo.housebak> --to <carpeta vacía>   # recupera un respaldo (pide la llave)
python -m house.app                         # la app en http://127.0.0.1:8765 (datos en ~/Library/Application Support/House)
```
- Python 3.11+, tipado con pydantic, `ruff` (línea 110). Pruebas con `pytest` para todo cambio de comportamiento.
- El catálogo de analitos está en `src/house/normalize/terminology.py`. **No inventes códigos LOINC**: déjalos en blanco si no estás seguro.
- Los adaptadores de OpenAI y Gemini están **sin verificar** contra la API real.
- Antes de subir: pruebas y ruff en verde. Commits claros en español o inglés; nunca fuerces push a `main`.

## Estado y siguientes pasos
Fase 0 lista (plan, proveedores, anonimizador, normalización, banco de pruebas). En curso: **Fase 0b**, Eduardo pasa estudios reales y Claude arma los casos en `bench/private/`; el banco mide a Claude contra la línea base. **Fase 1 en curso** en paralelo (ver `docs/TECHNICAL_PLAN.md` §2.6): base de la app hecha; sigue subir estudio y revisión lado a lado. PDFs escaneados requieren OCR local (Fase 1).
