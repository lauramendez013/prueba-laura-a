# app/agents/recolector_identidad.py
import logging
import re
from app.state import InmuebleState
from app.utils.messages import ensure_list_messages, get_last_text
from app.utils.intent import interpretar_identidad_y_proposito 
from app.utils.charlas import generar_respuesta_contextual

logger = logging.getLogger("RECOLECTOR_IDENTIDAD")

def recolector_identidad_agent(state: InmuebleState):
    """
    Agente de Recolección de Identidad.
    Lógica: Técnica y de estado.
    Comunicación: 100% delegada al LLM vía generar_respuesta_contextual.
    """

    state["modo"] = "registro"
    mensajes = ensure_list_messages(state.get("messages"))
    ultimo = (get_last_text(mensajes) or "").strip()
    ultimo_lc = ultimo.lower()

    datos = state.get("datos_inmueble", {}) or {}
    telefono_ws = (state.get("user_phone") or "").strip()
    tipo_cliente = state.get("tipo_cliente", "nuevo")

    if telefono_ws:
        datos["telefono_propietario"] = telefono_ws

    hubo_cambio = False

    # ==================================================
    # 0. RECUPERACIÓN PARA CLIENTES CONOCIDOS
    # ==================================================
    if tipo_cliente in ["contacto", "prospecto"]:
        if not datos.get("nombre_propietario") and state.get("nombre_zoho_inicial"):
            datos["nombre_propietario"] = state.get("nombre_zoho_inicial")
        if not datos.get("email_propietario") and state.get("email_zoho_inicial"):
            datos["email_propietario"] = state.get("email_zoho_inicial")

    # ==================================================
    # 1. INTELIGENCIA DE EXTRACCIÓN (CEREBRO LLM)
    # ==================================================
    ident = None
    try:
        # 1. Busca el papelito (caché) que dejó el Router
        cache = datos.pop("ident_check_cache", None)
        
        if cache:
            # 2. Si hay papelito, lo lee rápido sin usar la IA
            from app.utils.intent import IdentidadYPropositoOut
            ident = IdentidadYPropositoOut(**cache)
        else:
            # 3. Solo usa la IA si no le dejaron ningún papelito
            ident = interpretar_identidad_y_proposito(
                mensajes,
                nombre_actual=datos.get("nombre_propietario", ""),
                email_actual=datos.get("email_propietario", ""),
                cedula_actual=datos.get("cedula_propietario", ""), 
            )
    except Exception as e:
        logger.error(f"Error interpretando identidad: {e}")

    # ==================================================
    # 2. PROCESAR ACTUALIZACIONES (Lógica Técnica)
    # ==================================================
    if ident:
        if ident.nueva_cedula:
            if datos.get("cedula_propietario") != ident.nueva_cedula:
                datos["cedula_propietario"] = ident.nueva_cedula
                hubo_cambio = True
        if ident.nuevo_nombre:
            nuevo_nombre = ident.nuevo_nombre.title().strip()
            if datos.get("nombre_propietario") != nuevo_nombre:
                datos["nombre_propietario"] = nuevo_nombre
                hubo_cambio = True
        if ident.nuevo_email:
            nuevo_correo = ident.nuevo_email.lower().strip()
            if datos.get("email_propietario") != nuevo_correo:
                datos["email_propietario"] = nuevo_correo
                hubo_cambio = True

    # ==================================================
    # 3. GESTIÓN DE RESPUESTAS DINÁMICAS (LLM)
    # ==================================================
    
    if ident:
        # Evaluamos si quiere actualizar pero la IA no extrajo el dato nuevo
        quiere_actualizar_incompleto = (
            getattr(ident, 'quiere_actualizar_datos_generico', False) or 
            (getattr(ident, 'actualizar_nombre', False) and not ident.nuevo_nombre) or 
            (getattr(ident, 'actualizar_email', False) and not ident.nuevo_email) or 
            (getattr(ident, 'actualizar_cedula', False) and not ident.nueva_cedula)
        )

        contexto_especial = ""
        
        if getattr(ident, 'quiere_ver_datos', False):
            contexto_especial = f"Datos actuales: Nombre: {datos.get('nombre_propietario')}, Email: {datos.get('email_propietario')}, Cédula: {datos.get('cedula_propietario')}."
        
        elif getattr(ident, 'quiere_cambiar_celular', False):
            contexto_especial = "Explícale amablemente que, por motivos de seguridad, el número de celular está vinculado a su cuenta de WhatsApp y no se puede modificar (aclara que sí puede cambiar su nombre, cédula o correo si lo desea)."
            
        elif quiere_actualizar_incompleto:
            if getattr(ident, 'quiere_actualizar_datos_generico', False):
                contexto_especial = "El usuario quiere actualizar sus datos pero no especificó cuáles. Pregúntale amablemente qué dato desea cambiar (aclara que puede actualizar su nombre, cédula o correo electrónico)."
            elif getattr(ident, 'actualizar_nombre', False) and not ident.nuevo_nombre:
                contexto_especial = "El usuario indicó que quiere actualizar su nombre. Pregúntale cuál es su nuevo nombre completo."
            elif getattr(ident, 'actualizar_cedula', False) and not ident.nueva_cedula:
                contexto_especial = "El usuario indicó que quiere actualizar su cédula. Pregúntale cuál es su nuevo número de documento."
            elif getattr(ident, 'actualizar_email', False) and not ident.nuevo_email:
                contexto_especial = "El usuario indicó que quiere actualizar su correo. Pregúntale cuál es su nuevo correo electrónico."
            else:
                contexto_especial = "Pregúntale exactamente qué dato específico desea actualizar."
        
        if contexto_especial:
            respuesta_llm = generar_respuesta_contextual(mensajes, ultimo, datos.get("nombre_propietario", ""), contexto_especial)
            return {**state, "messages": mensajes + [respuesta_llm], "datos_inmueble": datos, "next_agent": "__end__"}

    # 3.2. Detección de Faltantes y Objeciones
    faltantes = []
    if not datos.get("nombre_propietario"): faltantes.append("nombre completo")
    if not datos.get("email_propietario"): faltantes.append("correo electrónico")
    if not datos.get("cedula_propietario"): faltantes.append("número de documento")

    ultimo_limpio = re.sub(r'[^a-záéíóúñ\s]', '', ultimo_lc).strip()
    palabras_negativas = ["no quiero", "para que", "porque", "obligatorio", "no te voy a dar", "no tengo", "quieor", "borralo", "borrar"]
    es_objecion = any(p in ultimo_limpio for p in palabras_negativas)

    if faltantes or es_objecion:
        contexto_llm = ""
        se_queja_cedula = es_objecion and ("cedula" in ultimo_lc or "documento" in ultimo_lc)
        se_queja_correo = es_objecion and ("correo" in ultimo_lc or "email" in ultimo_lc)

        if se_queja_cedula and datos.get("cedula_propietario"):
            contexto_llm = f"El usuario duda de dar la cédula, pero dile que esté tranquilo porque YA la tenemos registrada de forma segura: {datos.get('cedula_propietario')}. No se la estás pidiendo de nuevo."
        elif se_queja_correo and datos.get("email_propietario"):
            contexto_llm = f"El usuario no quiere dar su correo o quiere borrarlo. Dile que esté tranquilo porque ya lo tenemos registrado de forma segura ({datos.get('email_propietario')}) y no se lo estás pidiendo de nuevo."
        elif es_objecion:
            if faltantes:
                contexto_llm = f"El usuario se niega a dar datos. Explica breve y firmemente que por seguridad necesitamos: {', '.join(faltantes)} para continuar con su solicitud."
            else:
                contexto_llm = "El usuario se queja, pero ya tenemos todos sus datos. Confírmale que ya está todo listo para avanzar."
        else:
            target = faltantes[0]
            contexto_llm = f"Falta el {target}. Pídelo amablemente por políticas de seguridad."

        respuesta_llm = generar_respuesta_contextual(mensajes, ultimo, datos.get("nombre_propietario", ""), contexto_llm)
        return {**state, "messages": mensajes + [respuesta_llm], "datos_inmueble": datos, "next_agent": "__end__"}

    # ==================================================
    # 4. TRANSICIÓN AL EJECUTOR (DATOS COMPLETOS)
    # ==================================================
    logger.info("✅ Registro completo. Saltando al ejecutor para Zoho.")
    return {
        **state,
        "datos_inmueble": datos,
        "next_agent": "ejecutor_identidad",
    }

__all__ = ["recolector_identidad_agent"]