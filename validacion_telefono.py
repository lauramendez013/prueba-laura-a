# app/agents/validacion_telefono.py
from __future__ import annotations

from typing import Any, Dict
import re
import os
import logging

from app.tools.zoho_search import (
    buscar_lead_por_telefono,
    buscar_contacto_por_telefono,
)

# ==================================================
# LOGGER
# ==================================================

logger = logging.getLogger("VALIDACION_TELEFONO")

# ==================================================
# HELPERS
# ==================================================

E164 = re.compile(r"^\+?\d{7,15}$")


def _normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None

    p = re.sub(r"\D+", "", phone)
    if not p:
        return None

    if len(p) == 10 and not p.startswith("57"):
        p = "57" + p

    if not p.startswith("+"):
        p = "+" + p

    if not E164.match(p):
        return None

    return p


# ==================================================
# AGENTE
# ==================================================
def validacion_telefono_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    PRIMER NODO DEL GRAFO.
    Se ejecuta SOLO UNA VEZ por sesión.
    """

    if state.get("telefono_validado"):
        logger.info("Teléfono ya validado. Saltando validación.")
        return {
            **state,
            "next_agent": "router",
        }

    datos = dict(state.get("datos_inmueble") or {})

    logger.info("Iniciando validación de teléfono (Zoho)")

    raw_phone = state.get("user_phone") or datos.get("telefono_propietario")
    phone = _normalize_phone(raw_phone)

    logger.info(
        f"Teléfono | raw='{raw_phone}' | normalizado='{phone}' | session='{state.get('session_id')}'"
    )

    # ===============================
    # TELÉFONO NO VÁLIDO
    # ===============================
    if not phone:
        logger.warning("Teléfono no válido. Flujo router.")
        state.update({
            "datos_inmueble": datos,
            "cliente_existente": False,
            "tipo_cliente": "nuevo",
            "telefono_validado": True,
            "next_agent": "router", # 
        })
        return state

    # Persistimos teléfono
    datos["telefono_propietario"] = phone
    datos["telefono_confirmado"] = True

    api_base = os.environ.get("ZOHO_API_BASE", "https://www.zohoapis.com")

    contacto = None
    lead = None

    try:
        logger.info(f"Zoho Contactos → {phone}")
        contacto = buscar_contacto_por_telefono(api_base, phone)

        if not contacto:
            logger.info(f"Zoho Leads → {phone}")
            lead = buscar_lead_por_telefono(api_base, phone)

    except Exception:
        logger.error("Error crítico consultando Zoho", exc_info=True)

    # ===============================
    # CONTACTO EXISTENTE
    # ===============================
    if contacto:
        logger.info(f"CONTACTO encontrado | ID={contacto.get('id')}")

        datos.update({
            "nombre_propietario": contacto.get("nombre"),
            "email_propietario": contacto.get("raw", {}).get("Email"),
            "cedula_propietario": contacto.get("raw", {}).get("No_Documento"),
            "zoho_contact_id": contacto.get("id"),
        })

        state.update({
            "datos_inmueble": datos,
            "cliente_existente": True,
            "tipo_cliente": "contacto",
            "politica_aceptada": True,
            "identidad_completa": True,
            "telefono_validado": True,
            "next_agent": "router", 
        })
        return state

    # ===============================
    # PROSPECTO
    # ===============================
    if lead:
        logger.info(f"PROSPECTO encontrado | ID={lead.get('id')}")

        # Buscamos si ya aceptó Habeas Data en Zoho
        registro_zoho = lead.get("raw", {})
        acepta_habeas = registro_zoho.get("Acepta_Habeas_Data")
        
        # Normalizamos por si viene como "SI", "Si", "sí", o un booleano True
        ya_acepto = False
        if isinstance(acepta_habeas, str) and acepta_habeas.strip().upper() in ["SI", "SÍ", "TRUE"]:
            ya_acepto = True
        elif acepta_habeas is True:
            ya_acepto = True

        datos.update({
            "nombre_propietario": lead.get("nombre"),
            "email_propietario": registro_zoho.get("Email"),
            "cedula_propietario": registro_zoho.get("No_Documento"),
            "zoho_lead_id": lead.get("id"),
        })

        if ya_acepto:
            logger.info("El prospecto ya había aceptado Habeas Data. Va al router.")
            state.update({
                "datos_inmueble": datos,
                "cliente_existente": True,
                "tipo_cliente": "prospecto",
                "identidad_completa": False,
                "telefono_validado": True,
                "politica_aceptada": True,
                "next_agent": "router",
            })
        else:
            logger.info("El prospecto NO ha aceptado Habeas Data. Va al router (él lo mandará a política).")
            state.update({
                "datos_inmueble": datos,
                "cliente_existente": True,
                "tipo_cliente": "prospecto",
                "identidad_completa": False,
                "telefono_validado": True,
                "politica_aceptada": False,
                "next_agent": "router", 
            })
            
        return state

    # ===============================
    # CLIENTE NUEVO
    # ===============================
    logger.info("No existe en Zoho. Cliente NUEVO. Enviando al router.")

    state.update({
        "datos_inmueble": datos,
        "cliente_existente": False,
        "tipo_cliente": "nuevo",
        "identidad_completa": False,
        "telefono_validado": True,
        "next_agent": "router", 
    })
    return state

__all__ = ["validacion_telefono_agent"]