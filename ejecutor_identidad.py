# app/agents/ejecutor_identidad.py

from app.state import InmuebleState
from app.utils.messages import ensure_list_messages, get_last_text
import logging

logger = logging.getLogger("EJECUTOR_IDENTIDAD")

def ejecutor_identidad_agent(state: InmuebleState):
    """
    Agente final del flujo de identidad.
    Maneja el registro en Zoho CRM y lanza la tarjeta de resumen final.
    """

    try:
        from app.tools.contactos import (
            guardar_contacto_y_oportunidad_api,
            actualizar_registro_zoho_api,
            crear_oportunidad_manual_api,
        )
        from app.tools.conversion import ejecutar_conversion_prospecto_api
    except Exception:
        logger.exception("❌ ERROR IMPORTANDO TOOLS ZOHO EN EJECUTOR")
        return {
            **state,
            "messages": ensure_list_messages(state.get("messages")) + [
                "Lo siento, ocurrió un problema interno al conectar con el sistema."
            ],
            "next_agent": "__end__",
        }

    mensajes = ensure_list_messages(state.get("messages"))
    ultimo = (get_last_text(mensajes) or "").strip().lower()
    datos = dict(state.get("datos_inmueble", {}) or {})
    busqueda = datos.get("busqueda", {})

    telefono = (state.get("user_phone") or "").strip()
    datos["telefono_propietario"] = telefono 

    # ==================================================
    # 1. DATOS MÍNIMOS OBLIGATORIOS
    # ==================================================
    nombre = datos.get("nombre_propietario", "").strip()
    email = datos.get("email_propietario", "").strip()
    cedula = datos.get("cedula_propietario", "").strip() 

    if not (nombre and email and cedula): 
        logger.warning("Faltan datos (nombre/email/cedula). Regresando a recolector.")
        return {
            **state,
            "messages": mensajes,
            "datos_inmueble": datos,
            "next_agent": "recolector_identidad",
        }

   
    if "|" in nombre:
        nombre = nombre.split("|")[-1].strip()

    partes_nombre = nombre.split(" ", 1)
    first_name = partes_nombre[0].title()
    
    # Cortamos a un máximo de 75 caracteres para evitar rechazos en Zoho
    raw_last_name = partes_nombre[1].title() if len(partes_nombre) > 1 else "."
    last_name = raw_last_name[:75] 

    zoho_contact_id = datos.get("zoho_contact_id") or state.get("zoho_contact_id")
    zoho_lead_id = datos.get("zoho_lead_id") or state.get("zoho_lead_id")
    
    if zoho_contact_id:
        tipo_cliente = "contacto"
    elif zoho_lead_id:
        tipo_cliente = "prospecto"
    else:
        tipo_cliente = "nuevo"

    # ==================================================
    # 2. ACTUALIZACIÓN DE PERFIL EN ZOHO
    # ==================================================
    datos_actualizar = {
        "First_Name": first_name, 
        "Last_Name": last_name,  
        "Email": email,
        "No_Documento": cedula, 
    }

    if tipo_cliente == "contacto" and zoho_contact_id:
        actualizar_registro_zoho_api("Contacts", zoho_contact_id, datos_actualizar)
    elif tipo_cliente == "prospecto" and zoho_lead_id:
        actualizar_registro_zoho_api("Leads", zoho_lead_id, datos_actualizar)

    # ==================================================
    # 3. DETERMINACIÓN DE PROPÓSITO
    # ==================================================
    proposito_actual = datos.get("proposito") or busqueda.get("proposito")
    codigo_inmueble = datos.get("id_inmueble")

    if not proposito_actual:
        if any(w in ultimo for w in ["arrendar", "arriendo", "alquilar"]):
            proposito_actual = "Arriendo"
        elif any(w in ultimo for w in ["comprar", "compra", "venta", "vender"]):
            proposito_actual = "Venta"
        elif codigo_inmueble:
            proposito_actual = "Arriendo"

    if proposito_actual:
        datos["proposito"] = proposito_actual
        busqueda["proposito"] = proposito_actual
        datos["busqueda"] = busqueda

    # ✅ CORRECCIÓN AQUÍ: Se cambiaron los ** por * para WhatsApp
    resumen_card = (
        f"¡Listo, {first_name}! Tus datos han sido guardados exitosamente en nuestro sistema: ✅\n\n"
        f"👤 *Nombre:* {nombre}\n"
        f"📧 *Correo:* {email}\n"
        f"🪪 *Documento:* {cedula}\n"
        f"📱 *Celular:* {telefono}\n\n"
    )

    # 🔥 MAGIA: Lógica limpia para no preguntar por inmuebles si no hay contexto
    if not proposito_actual:
        logger.info("🚦 Registro completo pero SIN PROPÓSITO. Mostrando Tarjeta y frenando.")
        
        tipo_inm = busqueda.get("tipo_inmueble")
        if tipo_inm:
            resumen_card += f"Me comentabas que buscas un {tipo_inm.lower()}. Para poder asesorarte mejor, ¿estás interesado en *arrendar* o *comprar*? 🏡"
        else:
            resumen_card += "¿En qué más te puedo ayudar el día de hoy?"

        return {
            **state,
            "messages": mensajes + [resumen_card],
            "datos_inmueble": datos,
            "cliente_existente": True,
            "identidad_completa": True,
            "operacion": None, # Liberamos operación
            "next_agent": "__end__",
        }

    # ==================================================
    # 4. EJECUCIÓN EN ZOHO (Deals/Oportunidades)
    # ==================================================
    plataforma = datos.get("plataforma", "Whatsapp")
    res = None

    if tipo_cliente == "contacto" and zoho_contact_id:
        logger.info(f"🚀 ESCENARIO C: Creando oportunidad manual para Contacto ID={zoho_contact_id}")
        res = crear_oportunidad_manual_api(
            contact_id=zoho_contact_id, nombre=nombre, proposito=proposito_actual,
            plataforma=plataforma, id_inmueble=codigo_inmueble, cedula=cedula, 
        )

    elif tipo_cliente == "prospecto" and zoho_lead_id:
        logger.info(f"🚀 ESCENARIO B: Convirtiendo prospecto ID={zoho_lead_id}")
        res = ejecutar_conversion_prospecto_api({
            "lead_id": zoho_lead_id, "nombre": nombre, "proposito": proposito_actual,
            "codigo_inmueble": codigo_inmueble, "plataforma": plataforma, "cedula": cedula, 
        })
        if res and res.get("success"):
            datos["zoho_contact_id"] = res.get("contact_id")
            tipo_cliente = "contacto"

    else:
        logger.info("🚀 ESCENARIO A: Creando contacto y oportunidad desde cero")
        res = guardar_contacto_y_oportunidad_api({
            "nombre": nombre, "email": email, "telefono": telefono,  
            "proposito": proposito_actual, "id_inmueble": codigo_inmueble,
            "plataforma": plataforma, "cedula": cedula, 
        })
        if res and res.get("success") and res.get("contact_id"):
            datos["zoho_contact_id"] = res["contact_id"]
            tipo_cliente = "contacto"

    # ==================================================
    # 5. RESPUESTA FINAL Y TRANSICIÓN A BÚSQUEDA
    # ==================================================
    if res and res.get("success"):
        logger.info(f"✅ Operación Zoho exitosa para {nombre}")
        deal_id = res.get("deal_id") or res.get("opportunity_id")
        if deal_id:
            datos["zoho_deal_id"] = deal_id

        if state.get("modo") == "busqueda_silenciosa":
            return {
                **state,
                "messages": mensajes,
                "datos_inmueble": {**datos, "procesado_en_zoho": True},
                "operacion": "busqueda",
                "next_agent": "recolector_busqueda",
            }

        tipo_inmueble = busqueda.get("tipo_inmueble") or "inmueble"
        ubicacion = busqueda.get("ubicacion_especifica") or ""
        
        texto_intencion = f"{proposito_actual.lower()} un {tipo_inmueble.lower()}"
        if ubicacion:
            texto_intencion += f" en {ubicacion}"

        resumen_card += f"Retomando lo que me comentaste, veo que buscas {texto_intencion}. Para ir afinando la búsqueda, cuéntame: ¿en qué ciudad lo buscas y qué presupuesto manejas? 🔍"

        return {
            **state,
            "messages": mensajes + [resumen_card],
            "datos_inmueble": {**datos, "procesado_en_zoho": True},
            "tipo_cliente": tipo_cliente,
            "cliente_existente": True,
            "identidad_completa": True,
            "operacion": "busqueda", 
            "modo": None,
            "next_agent": "__end__",
        }

    logger.error(f"Fallo en operación Zoho: {res}")
    return {
        **state,
        "messages": mensajes + ["Hubo un inconveniente al guardar tu información. Por favor intenta de nuevo."],
        "next_agent": "__end__",
    }

__all__ = ["ejecutor_identidad_agent"]