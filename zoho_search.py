import requests
import logging
from .zoho_auth import zoho_headers

logger = logging.getLogger(__name__)

# ==================================================
# HELPERS
# ==================================================

def _get_cedula(record: dict) -> str | None:
    for key, value in record.items():
        if key and isinstance(key, str):
            k = key.lower()
            if "cedul" in k or "document" in k:
                return value
    return None


def _strip_country(phone: str) -> str:
    """
    Normaliza el celular para búsqueda:
    +573204999996 -> 3204999996
    573204999996  -> 3204999996
    """
    phone = phone.strip()
    if phone.startswith("+57"):
        return phone[3:]
    if phone.startswith("57") and len(phone) > 10:
        return phone[2:]
    return phone


def _buscar_por_celular(api_domain: str, modulo: str, phone: str) -> dict | None:
    """
    BÚSQUEDA OFICIAL POR CELULAR
    Se usa `word` únicamente porque Zoho indexa ahí
    los campos Mobile / Phone / custom phone.
    El criterio sigue siendo el número de celular.
    """
    phone_clean = _strip_country(phone)

    url = f"{api_domain}/crm/v8/{modulo}/search"
    params = {"word": phone_clean}

    logger.info(
        "[ZOHO][%s] Buscando por celular: %s",
        modulo.upper(),
        phone_clean,
    )

    r = requests.get(
        url,
        headers=zoho_headers(),
        params=params,
        timeout=20
    )

    if r.status_code == 204:
        return None

    r.raise_for_status()
    data = r.json().get("data")
    return data[0] if data else None


# ==================================================
# LEADS
# ==================================================

def buscar_lead_por_telefono(api_domain: str, phone: str) -> dict | None:
    try:
        lead = _buscar_por_celular(api_domain, "Leads", phone)

        if not lead:
            logger.info("[ZOHO][LEAD] No se encontró prospecto para %s", phone)
            return None

        # 🧠 MAGIA: Juntamos el nombre y el apellido para que Ali lo reciba completo
        first_name = lead.get("First_Name") or ""
        last_name = lead.get("Last_Name") or ""
        nombre_completo = f"{first_name} {last_name}".strip().replace(" .", "").replace(". ", "")

        logger.info(
            "[ZOHO][LEAD] Prospecto encontrado id=%s nombre_completo=%s",
            lead.get("id"),
            nombre_completo,
        )

        return {
            "id": lead.get("id"),
            "nombre": nombre_completo, # ✅ AHORA ENVÍA EL NOMBRE COMPLETO
            "apellido": last_name, 
            "cedula": _get_cedula(lead),
            "tipo": "prospecto",
            "raw": lead,
        }

    except requests.RequestException as e:
        logger.error(
            "[ZOHO][LEAD] Error consultando prospecto para %s: %s",
            phone,
            str(e),
            exc_info=True,
        )
        raise


# ==================================================
# CONTACTOS
# ==================================================

def buscar_contacto_por_telefono(api_domain: str, phone: str) -> dict | None:
    try:
        contact = _buscar_por_celular(api_domain, "Contacts", phone)

        if not contact:
            logger.info("[ZOHO][CONTACTO] No se encontró contacto para %s", phone)
            return None

        # 🧠 MAGIA: Juntamos el nombre y el apellido para que Ali lo reciba completo
        first_name = contact.get("First_Name") or ""
        last_name = contact.get("Last_Name") or ""
        nombre_completo = f"{first_name} {last_name}".strip().replace(" .", "").replace(". ", "")

        logger.info(
            "[ZOHO][CONTACTO] Contacto encontrado id=%s nombre_completo=%s",
            contact.get("id"),
            nombre_completo,
        )

        return {
            "id": contact.get("id"),
            "nombre": nombre_completo, 
            "apellido": last_name,
            "cedula": _get_cedula(contact),
            "tipo": "contacto",
            "raw": contact,
        }

    except requests.RequestException as e:
        logger.error(
            "[ZOHO][CONTACTO] Error consultando contacto para %s: %s",
            phone,
            str(e),
            exc_info=True,
        )
        raise

# ==================================================
# PRODUCTOS (INMUEBLES)
# ==================================================

def buscar_producto_por_codigo_coninsa(api_domain: str, codigo_coninsa: str) -> str | None:
    """
    Busca en el módulo Products el código de Coninsa para obtener el ID de Zoho.
    Esto permite llenar campos tipo Lookup sin errores de 'Invalid Data'.
    """
    try:
        url = f"{api_domain}/crm/v8/Products/search"
        # Usamos 'word' porque Zoho indexa el campo C_digo_del_Inmueble ahí
        params = {"word": codigo_coninsa}

        logger.info(
            "[ZOHO][PRODUCT] Buscando ID interno para código: %s",
            codigo_coninsa,
        )

        r = requests.get(
            url,
            headers=zoho_headers(),
            params=params,
            timeout=20
        )

        if r.status_code == 204:
            logger.info("[ZOHO][PRODUCT] No se encontró producto para %s", codigo_coninsa)
            return None

        r.raise_for_status()
        data = r.json().get("data")
        
        if data:
            product_id = data[0].get("id")
            logger.info(
                "[ZOHO][PRODUCT] Producto encontrado id=%s para código %s",
                product_id,
                codigo_coninsa
            )
            return product_id
            
        return None

    except requests.RequestException as e:
        logger.error(
            "[ZOHO][PRODUCT] Error consultando producto para %s: %s",
            codigo_coninsa,
            str(e),
            exc_info=True,
        )
        raise