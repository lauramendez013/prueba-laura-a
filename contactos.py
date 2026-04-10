# app/tools/contactos.py

import os
import requests
import logging
from datetime import datetime, timedelta
from .zoho_auth import zoho_headers

logger = logging.getLogger("ZOHO_CONTACTOS")
ZOHO_API_BASE = os.environ.get("ZOHO_API_BASE", "https://www.zohoapis.com")

# ==================================================
# ESCENARIO A: CLIENTE NUEVO (WPP) -> CONTACTO + DEAL
# ==================================================
def guardar_contacto_y_oportunidad_api(data: dict) -> dict:
    try:
        nombre_completo = (data.get("nombre") or "Cliente Ali").strip()
        partes = nombre_completo.split(" ", 1)
        first_name = partes[0]
        last_name = partes[1] if len(partes) > 1 else "."

        telefono = (data.get("telefono") or "").strip()
        email = (data.get("email") or "").strip()
        cedula = (data.get("cedula") or "").strip() 
        
        proposito_raw = (data.get("proposito") or "arriendo").lower()
        tipo_servicio = "Venta" if "venta" in proposito_raw or "comprar" in proposito_raw else "Arriendo"
        
        id_inmueble = data.get("id_inmueble")
        plataforma = data.get("plataforma", "Whatsapp")

        # 1. CREACIÓN DEL CONTACTO (Layout 699988)
        payload_contacto = {
            "data": [{
                "Layout": {"id": "7112408000000091033"},
                "First_Name": first_name,
                "Last_Name": last_name,
                "Email": email or None,
                "Mobile": telefono,
                "Phone": telefono,
                "No_Documento": cedula or None, 
                "Acepta_Habeas_Data": "SI",
                "C_mo_se_realiz_el_contacto_registro": plataforma,
                "Sucursal": "Por definir"
            }]
        }

        logger.info(f"📤 Creando Contacto de Negocio Zoho → {telefono} (Cédula: {cedula})")
        r_con = requests.post(f"{ZOHO_API_BASE}/crm/v8/Contacts", headers=zoho_headers(), json=payload_contacto)
        if r_con.status_code >= 400:
            logger.error(f"❌ DETALLE ERROR ZOHO (Contacts): {r_con.text}")
        r_con.raise_for_status()

        contact_id = r_con.json()["data"][0]["details"]["id"]
        logger.info(f"  Contacto de negocio creado ID={contact_id}")

        # 2. CREACIÓN DEL DEAL (Layout 708508)
        fecha_cierre = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        prefix = f"{id_inmueble} - " if id_inmueble else ""
        nombre_deal = f"{prefix}{tipo_servicio} - {nombre_completo}"

        deal_data = {
            "Layout": {"id": "7112408000000708508"},
            "Contact_Name": {
                "id": contact_id,
                "Layout": {"id": "7112408000000091033"}
            },
            "Deal_Name": nombre_deal,
            "Stage": "Contactado",
            "Closing_Date": fecha_cierre,
            "Amount": 0,
            "Pipeline": "Standard (Standard)",
            "Tipo_de_servicio": tipo_servicio,
            "C_mo_se_realiz_el_contacto_registro": plataforma,
            "Sucursal": "Por definir",
            "Valor_canon_precio": "0",
            "Direcci_n_del_inmueble": "Por definir",
            "Tipo_de_Propiedad": "Por definir",
            "C_mo_se_enter_del_inmueble": "Por definir",      
            "Tipo_de_Oportunidad": "Por definir",             
            "Gesti_n_inicial_por": "Gestión Ali",             
            "Phone": telefono                                 
        }

        if id_inmueble:
            deal_data["C_digo_del_Inmueble"] = str(id_inmueble)

        logger.info(f"📤 Creando Deal → {nombre_deal}")
        r_deal = requests.post(f"{ZOHO_API_BASE}/crm/v8/Deals", headers=zoho_headers(), json={"data": [deal_data]})
        if r_deal.status_code >= 400:
            logger.error(f"❌ DETALLE ERROR ZOHO (Deals): {r_deal.text}")
        r_deal.raise_for_status()

        return {
            "success": True,
            "contact_id": contact_id,
            "deal_id": r_deal.json()["data"][0]["details"]["id"],
        }
    except Exception as e:
        logger.error("❌ Error en flujo completo Zoho", exc_info=True)
        return {"success": False, "error": str(e)}

# ==================================================
# ACTUALIZACIÓN DE REGISTRO ZOHO (PUT)
# ==================================================
def actualizar_registro_zoho_api(modulo: str, record_id: str, datos_actualizar: dict) -> bool:
    try:
        url = f"{ZOHO_API_BASE}/crm/v8/{modulo}/{record_id}"
        r = requests.put(url, headers=zoho_headers(), json={"data": [datos_actualizar]})
        
        # 🚨 ESTE ES EL CHISMOSO QUE NECESITAMOS VER EN TU CONSOLA
        if r.status_code not in (200, 201, 202):
            logger.error(f"❌ RECHAZO EXACTO DE ZOHO: Código {r.status_code} - Detalle: {r.text}")
            
        return r.status_code in (200, 201, 202)
    except Exception as e:
        logger.error(f"❌ Error actualizando registro en Zoho: {e}", exc_info=True)
        return False

# ==================================================
# ESCENARIO C: CONTACTO EXISTENTE → DEAL MANUAL
# ==================================================
def crear_oportunidad_manual_api(contact_id: str, nombre: str, proposito: str, plataforma: str = "Whatsapp", id_inmueble: str | None = None, cedula: str | None = None) -> dict:
    try:
        nombre = (nombre or "Cliente Ali").strip()
        tipo_servicio = "Venta" if "venta" in (proposito or "").lower() or "comprar" in (proposito or "").lower() else "Arriendo"

        fecha_cierre = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        prefix = f"{id_inmueble} - " if id_inmueble else ""
        nombre_deal = f"{prefix}{tipo_servicio} - {nombre}"

        deal_data = {
            "Layout": {"id": "7112408000000708508"},
            "Contact_Name": {
                "id": contact_id,
                "Layout": {"id": "7112408000000091033"}
            },
            "Deal_Name": nombre_deal,
            "Closing_Date": fecha_cierre,
            "Stage": "Contactado",
            "Amount": 0,
            "Pipeline": "Standard (Standard)",
            "Sucursal": "Por definir",
            "Tipo_de_servicio": tipo_servicio,
            "C_mo_se_realiz_el_contacto_registro": plataforma,
            "Valor_canon_precio": "0",
            "Direcci_n_del_inmueble": "Por definir",
            "Tipo_de_Propiedad": "Por definir",
            "C_mo_se_enter_del_inmueble": "Por definir",
            "Tipo_de_Oportunidad": "Por definir",
            "Gesti_n_inicial_por": "Gestión Ali"
        }

        if id_inmueble:
            deal_data["C_digo_del_Inmueble"] = str(id_inmueble)

        logger.info(f"📤 Creando Deal Manual → {nombre_deal}")
        r = requests.post(f"{ZOHO_API_BASE}/crm/v8/Deals", headers=zoho_headers(), json={"data": [deal_data]})
        if r.status_code >= 400:
            logger.error(f"❌ DETALLE ERROR ZOHO (Deals Manual): {r.text}")
        r.raise_for_status()

        return {"success": True, "deal_id": r.json()["data"][0]["details"]["id"]}
    except Exception as e:
        logger.error("❌ Error creando Deal manual", exc_info=True)
        return {"success": False, "error": str(e)}


# ==================================================
# NOTAS (NOTES) EN ZOHO - CORRECCIÓN V8 🚨
# ==================================================
def guardar_nota_zoho_api(deal_id: str, titulo: str, contenido: str) -> dict:
    """Crea una nota amarrada a un Deal. Corregido para estructura de objetos v8."""
    try:
        url = f"{ZOHO_API_BASE}/crm/v8/Notes"
        
        # 🧠 LA CORRECCIÓN: Zoho v8 requiere que 'module' dentro de 'Parent_Id' sea un OBJETO
        # con la llave 'api_name'.
        payload = {
            "data": [
                {
                    "Note_Title": titulo,
                    "Note_Content": contenido,
                    "Parent_Id": {
                        "id": str(deal_id),
                        "module": {
                            "api_name": "Deals"
                        }
                    }
                }
            ]
        }
        
        r = requests.post(url, headers=zoho_headers(), json=payload)
        
        if r.status_code >= 400:
            logger.error(f"❌ DETALLE ERROR ZOHO (Notes POST): {r.text}")
            
        r.raise_for_status()
        
        note_id = r.json()["data"][0]["details"]["id"]
        return {"success": True, "note_id": note_id}
    except Exception as e:
        logger.error("❌ Error creando Nota en Zoho", exc_info=True)
        return {"success": False, "error": str(e)}

def actualizar_nota_zoho_api(note_id: str, titulo: str, contenido: str) -> bool:
    """Sobrescribe una nota existente."""
    try:
        url = f"{ZOHO_API_BASE}/crm/v8/Notes/{note_id}"
        payload = {
            "data": [
                {
                    "Note_Title": titulo,
                    "Note_Content": contenido
                }
            ]
        }
        r = requests.put(url, headers=zoho_headers(), json=payload)
        return r.status_code in (200, 201, 202)
    except Exception:
        logger.error(f"❌ Error actualizando Nota en Zoho", exc_info=True)
        return False

__all__ = [
    "guardar_contacto_y_oportunidad_api",
    "actualizar_registro_zoho_api",
    "crear_oportunidad_manual_api",
    "guardar_nota_zoho_api",
    "actualizar_nota_zoho_api"
]