# app/agents/bienvenida.py

from app.state import InmuebleState
from app.utils.messages import ensure_list_messages, get_last_text
from app.utils.charlas import generar_respuesta_contextual

def bienvenida_agent(state: InmuebleState):
    """
    Bienvenida unificada y contextual (Delegada al LLM para sonar humana):
    - Muestra resumen si hay datos.
    - Si el usuario ya dio una intención previa (pesca temprana), la usa para el contexto.
    - Ofrece actualizar o pide faltantes basándose en la personalidad del prompt maestro.
    """

    mensajes = ensure_list_messages(state.get("messages")) #Obtenemos la lista de mensajes y extrae lo ultimo que dijo el usuario
    ultimo_usuario = get_last_text(mensajes) or "Hola" #si por alguna razoón esta vacio asume que dijo hola para no romper el flujo...
    datos = dict(state.get("datos_inmueble", {}) or {})
    busqueda = datos.get("busqueda", {})

    cliente_existente = state.get("cliente_existente", False)
    identidad_completa = state.get("identidad_completa", False)

    
    #Si encuentra ese separador, toma solo la última parte (el nombre real) y usa .strip() para quitar espacios.
    nombre_completo = (datos.get("nombre_propietario") or "").strip()
    if "|" in nombre_completo:
        nombre_completo = nombre_completo.split("|")[-1].strip()
        
    primer_nombre = nombre_completo.split(" ")[0] if nombre_completo else ""

    email = (datos.get("email_propietario") or "").strip()
    cedula = (datos.get("cedula_propietario") or "").strip() 
    telefono = (state.get("user_phone") or "").strip()

    # CONSTRUCCIÓN DE LA INTENCIÓN PREVIA (evisa la memoria de busqueda para ver si el router logró capturar alguna intención del primer mensaje del usuario.)
    proposito_previo = busqueda.get("proposito")
    tipo_inmueble = busqueda.get("tipo_inmueble") or "inmueble"
    ubicacion = busqueda.get("ubicacion_especifica") or ""

    texto_intencion = ""
    if proposito_previo:
        texto_intencion = f"{proposito_previo.lower()} un {tipo_inmueble.lower()}"
        if ubicacion:
            texto_intencion += f" en {ubicacion}"


    # CONSTRUCCIÓN DEL CONTEXTO PARA EL LLM
    # Validamos manualmente si realmente faltan datos vitales, por si 'identidad_completa' viene False
    faltan_datos = not (nombre_completo and email and cedula)

    if cliente_existente and not faltan_datos:
        # ESCENARIO 1: Cliente conocido y con TODO completo. 
        contexto_llm = (
            "ESTE ES EL PRIMER MENSAJE DE LA CONVERSACIÓN. "
            f"1. Saluda al cliente explícitamente por su nombre ('{primer_nombre}') de forma amigable, profesional y empática. "
            "Preséntate como Ali de Coninsa Inmobiliaria (menciona brevemente que tenemos más de 50 años de experiencia). "
        )
        if texto_intencion:
            contexto_llm += f"2. Menciona que viste que está buscando {texto_intencion}. "
            contexto_llm += "3. Pregúntale cómo le puedes ayudar hoy con esa búsqueda."
        else:
            contexto_llm += "2. Pregúntale en qué le puedes ayudar el día de hoy."
            
        contexto_llm += " IMPORTANTE: NO le muestres sus datos personales en este mensaje, a menos que él pida explícitamente verlos o actualizarlos."

    elif cliente_existente and faltan_datos:
        # ESCENARIO 2: Cliente conocido, pero le faltan datos (ej. la cédula).
        contexto_llm = (
            "ESTE ES EL PRIMER MENSAJE DE LA CONVERSACIÓN. "
            f"1. Saluda al cliente explícitamente por su nombre ('{primer_nombre}') de forma amigable, profesional y empática. "
            "Preséntate como Ali de Coninsa Inmobiliaria. "
            "2. Muéstrale los datos que tienes registrados:\n"
            f"   Nombre: {nombre_completo or 'Pendiente'}\n"
            f"   Correo: {email or 'Pendiente'}\n"
            f"   Documento: {cedula or 'Pendiente'}\n"
            f"   Celular: {telefono}\n"
        )
        
        if not cedula:
            contexto_llm += "3. Como el documento (cédula) está pendiente, pídeselo y explícale que es por seguridad para continuar. "
        else:
            contexto_llm += "3. Pídele el dato que falta (correo o nombre) por seguridad para continuar. "
            
        if texto_intencion:
            contexto_llm += f"Menciónale que luego de completar sus datos, podrán empezar a buscar su {texto_intencion}. "

    else:
        # ESCENARIO 3: Cliente completamente nuevo (Sin Identidad)
        contexto_llm = (
            "ESTE ES EL PRIMER MENSAJE DE BIENVENIDA PARA UN CLIENTE NUEVO. "
            "1. Saluda de forma amigable, profesional y empática. Preséntate como Ali, asesora de Coninsa Inmobiliaria "
            "(menciona brevemente los 50 años de experiencia). "
        )
        if texto_intencion:
            contexto_llm += f"2. Menciona que viste que le interesa {texto_intencion}. "
        
        contexto_llm += (
            "3. Pídele que, para darle la mejor atención y empezar, te comparta estos 3 datos básicos: "
            "su nombre completo, su correo electrónico y su número de documento (cédula). "
            f"4. Aclárale que ya tienes registrado su celular de WhatsApp ({telefono})."
        )


    # GENERACIÓN DINÁMICA DE LA RESPUESTA
    respuesta_dinamica = generar_respuesta_contextual(mensajes, ultimo_usuario, primer_nombre, contexto_llm)

    return {
        **state,
        "messages": mensajes + [respuesta_dinamica],
        "operacion": "registro",
        "modo": "registro",
        "next_agent": "__end__",
    }

__all__ = ["bienvenida_agent"]