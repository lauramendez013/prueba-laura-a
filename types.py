# app/types.py
from enum import Enum
from typing import Dict


class AgentName(str, Enum):
    VALIDACION_TELEFONO = "validacion_telefono"
    AGENTE_POLITICA     = "agente_politica"
    BIENVENIDA          = "bienvenida"
    ROUTER              = "router"

    RECOLECTOR_IDENTIDAD = "recolector_identidad"
    RECOLECTOR_REGISTRO  = "recolector_registro"
    RECOLECTOR_CONSULTA  = "recolector_consulta"

    EJECUTOR_IDENTIDAD   = "ejecutor_identidad"
    EJECUTOR_REGISTRO    = "ejecutor_registro"
    EJECUTOR_CONSULTA    = "ejecutor_consulta"

    END = "__end__"  # para el valor especial de fin


def goto(agent: AgentName) -> Dict[str, str]:
    """
    Helper para estandarizar cómo seteamos next_agent en el estado.
    """
    return {"next_agent": agent.value}
