#!/bin/zsh
# Abre House en esta Mac (doble clic en Finder). Cierra esta ventana para apagarlo.
cd "$(dirname "$0")" && source .venv/bin/activate && python -m house.app
