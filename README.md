# House

Expediente de salud familiar con IA: laboratorios, imágenes, recetas y expediente clínico en una línea de tiempo por persona. Uso personal y familiar.

- Definición de producto: [docs/PRODUCT.md](docs/PRODUCT.md)

## Regla de oro
Este repositorio **nunca** contiene datos de salud reales (ni documentos, ni capturas, ni fixtures con datos verdaderos). Los datos viven fuera del repo, cifrados.

## Estado
Fase 0: plan técnico, esquema de datos, capa de proveedores de IA intercambiable, anonimizador, normalización de unidades y rangos, pipeline de extracción y banco de pruebas.

- Plan técnico: [docs/TECHNICAL_PLAN.md](docs/TECHNICAL_PLAN.md)
- Maqueta interactiva (datos ficticios): [docs/mockup/house-mockup.html](docs/mockup/house-mockup.html)
- Banco de pruebas: [bench/README.md](bench/README.md)

## Desarrollo
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                      # pruebas
ruff check . && ruff format --check .
python -m house.bench          # banco de pruebas offline (línea base sin IA)
```
Para usar proveedores reales: `pip install -e ".[anthropic]"`, copia `config/house.example.toml` a `config/house.toml` y exporta la clave del proveedor (por ejemplo `ANTHROPIC_API_KEY`) en tu entorno.
