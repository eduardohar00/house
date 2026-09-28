# Banco de pruebas de extracción

Mide qué tan bien cada proveedor de IA convierte un documento de laboratorio en datos
estructurados, **antes** de confiarle tus estudios.

## Qué mide
| Métrica | Por qué importa |
|---|---|
| Valores correctos / esperados | Exactitud de lo que se guardaría |
| Faltantes y filas extra | Qué se pierde y qué se inventa |
| Filas sin respaldo | Filas cuya evidencia no existe literalmente en el documento (alucinación) |
| A revisar | Carga de trabajo humano (conversiones de unidades, unidades raras, valores implausibles) |
| Fugas de datos personales | Cuántos textos prohibidos llegaron al proveedor. Debe ser 0 |
| Costo y latencia | Presupuesto mensual y experiencia de uso |

## Casos
Cada caso es una carpeta en `bench/cases/<id>/` con:
- `document.txt` o `document.pdf`: el documento.
- `expected.json`: lo correcto, con claves canónicas (`src/house/normalize/terminology.py`) y unidades canónicas.
- `profile.json` (opcional): `names` (nombres a quitar) y `forbidden` (textos que NUNCA deben llegar a la IA).

`bench/cases/example-synthetic/` es un documento **inventado** que sirve de ejemplo y de prueba automática.

## Cómo correrlo con tus documentos reales (en tu Mac)
Tus documentos reales **nunca** se suben al repositorio ni a esta sesión.

1. Instala: `pip install -e ".[dev,pdf,anthropic]"`.
2. Copia `config/house.example.toml` a `config/house.toml` y completa los proveedores que quieras comparar. Las claves de API van en variables de entorno (por ejemplo `ANTHROPIC_API_KEY`).
3. Crea tus casos en `bench/private/<id>/` (está en `.gitignore`). Para cada PDF, escribe a mano `expected.json` con los valores correctos: es el trabajo que da valor al banco.
4. Corre: `python -m house.bench --cases bench/private --config config/house.toml --providers baseline-regex,claude,gpt --out bench/results/reporte.md`.

## Criterio para pasar de "revisión total" a "revisar solo lo marcado"
Con al menos 30 documentos: 100 % de valores correctos entre las filas que el sistema marca como confiables, 0 filas sin respaldo, 0 fugas de datos personales.
