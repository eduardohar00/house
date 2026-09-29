"""Contrato de extracción entre el documento, el proveedor de IA y la base de datos.

El modelo de IA devuelve filas "tal como aparecen" en el documento. Nada de lo que
devuelve se guarda sin pasar por verificación determinista (ver extract.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocumentType = Literal["laboratorio", "imagen", "receta", "nota_clinica", "vacuna", "otro"]


class RawRow(BaseModel):
    """Un resultado tal como aparece impreso en el documento."""

    analyte_name: str = Field(description="Nombre del análisis exactamente como está impreso")
    value_text: str = Field(
        description="Valor exactamente como está impreso, p. ej. '92', '1.01' o 'Negativo'"
    )
    unit_text: str | None = Field(default=None, description="Unidad impresa, p. ej. 'mg/dL'")
    ref_text: str | None = Field(default=None, description="Rango de referencia impreso, si existe")
    evidence: str = Field(description="La línea completa del documento de donde salió la fila")
    section: str | None = Field(
        default=None, description="Encabezado de la sección donde aparece, p. ej. 'EXAMEN GENERAL DE ORINA'"
    )


class RawExtraction(BaseModel):
    document_type: DocumentType
    collected_on: str | None = Field(
        default=None, description="Fecha de toma de la muestra, formato AAAA-MM-DD"
    )
    lab_name: str | None = None
    rows: list[RawRow]


def extraction_json_schema() -> dict:
    """Esquema JSON estricto para salidas estructuradas (todos los campos requeridos)."""
    nullable_str = {"type": ["string", "null"]}
    row = {
        "type": "object",
        "properties": {
            "analyte_name": {"type": "string"},
            "value_text": {"type": "string"},
            "unit_text": nullable_str,
            "ref_text": nullable_str,
            "evidence": {"type": "string"},
            "section": nullable_str,
        },
        "required": ["analyte_name", "value_text", "unit_text", "ref_text", "evidence", "section"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "document_type": {
                "type": "string",
                "enum": ["laboratorio", "imagen", "receta", "nota_clinica", "vacuna", "otro"],
            },
            "collected_on": nullable_str,
            "lab_name": nullable_str,
            "rows": {"type": "array", "items": row},
        },
        "required": ["document_type", "collected_on", "lab_name", "rows"],
        "additionalProperties": False,
    }
