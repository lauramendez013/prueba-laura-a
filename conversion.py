import os
import requests
import logging
from datetime import datetime, timedelta
from .zoho_auth import zoho_headers

# ✅ IMPORTAMOS LA FUNCIÓN QUE SÍ SABE CREAR DEALS CON EL LAYOUT CORRECTO
from .contactos import crear_oportunidad_manual_api 

logger = logging.getLogger("ZOHO_CONVERSION")
ZOHO_API_BASE = os.environ.get("ZOHO_API_BASE", "https://www.zohoapis.com")


def ejecutar_conversion_prospecto_api(info: dict) -> dict:
    """
    ESCENARIO B:
    Convierte un Prospecto (Lead) existente en Zoho CRM a Contacto.
    LUEGO, crea su Oportunidad (Deal) manualmente para saltar bloqueos de Layout.
    """

    lead_id = info.get("lead_id")
    nombre = (info.get("nombre") or "Cliente Ali").strip()
    cedula = (info.get("cedula") or "").strip() 

    proposito_raw = (info.get("proposito") or "arriendo").lower()
    tipo_servicio = "Venta" if "venta" in proposito_raw or "comprar" in proposito_raw else "Arriendo"

    codigo_inmueble = info.get("codigo_inmueble")
    plataforma = info.get("plataforma", "Whatsapp")

    if not lead_id:
        logger.error("❌ Error: No se recibió un lead_id para convertir.")
        return {"success": False, "error": "lead_id faltante"}

    url = f"{ZOHO_API_BASE}/crm/v8/Leads/{lead_id}/actions/convert"

    # ==================================================
    # PASO 1: CONVERTIR EL LEAD A CONTACTO (Sin meter el Deal aquí)
    # ==================================================
    conversion_data = {
        "overwrite": True,
        "notify_lead_owner": True,
        "notify_new_entity_owner": True
    }

    payload = {
        "data": [
            conversion_data
        ]
    }

    try:
        logger.info(f"🔄 PASO 1: Convirtiendo Lead {lead_id} a Contacto...")
        response = requests.post(
            url,
            headers=zoho_headers(),
            json=payload,
            timeout=20,
        )

        if response.status_code >= 400:
            logger.error(f"❌ DETALLE ERROR ZOHO (Convert): {response.text}")
            return {"success": False, "error": response.text}

        res_json = response.json()
        
        # ✅ CORRECCIÓN DE LECTURA: Buscamos el ID donde Zoho realmente lo guarda
        data_resp = res_json.get("data", [{}])[0]
        details = data_resp.get("details", {})
        
        # Intentamos sacarlo de "details" primero
        contact_id = details.get("Contacts") or data_resp.get("Contacts")
        
        if isinstance(contact_id, dict):
            contact_id = contact_id.get("id")
        
        if not contact_id:
             logger.error(f"❌ Zoho no devolvió el ID del Contacto. JSON completo: {res_json}")
             return {"success": False, "error": "No se obtuvo ID del contacto tras la conversión"}

        logger.info(f"✅ Conversión exitosa. Nuevo Contacto ID: {contact_id}")

        # ==================================================
        # PASO 2: CREAR EL DEAL CON EL LAYOUT CORRECTO USANDO LA FUNCIÓN MANUAL
        # ==================================================
        logger.info("🔄 PASO 2: Generando el Deal (Oportunidad) con el Layout de Coninsa...")
        
        res_deal = crear_oportunidad_manual_api(
             contact_id=contact_id,
             nombre=nombre,
             proposito=tipo_servicio,
             plataforma=plataforma,
             id_inmueble=codigo_inmueble,
             cedula=cedula
        )

        if res_deal.get("success"):
             logger.info("✅ Deal creado exitosamente después de la conversión.")
             return {
                 "success": True,
                 "contact_id": contact_id,
                 "deal_id": res_deal.get("deal_id")
             }
        else:
             logger.warning(f"⚠️ El Lead se convirtió, pero falló la creación manual del Deal: {res_deal.get('error')}")
             return {
                 "success": True, 
                 "contact_id": contact_id, 
                 "deal_error": res_deal.get("error")
             }

    except Exception as e:
        logger.error("❌ Error crítico en conversión Zoho", exc_info=True)
        return {"success": False, "error": str(e)}