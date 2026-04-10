# app/utils/charlas.py

from langchain_core.prompts import ChatPromptTemplate
from app.config import get_llm
from app.tools.agente_db import obtener_prompt_agente_sync

def generar_respuesta_contextual(mensajes, ultimo_usuario: str, nombre: str, contexto_proceso: str = "") -> str:
    """
    Motor de voz centralizado de Ali. 
    Usa el prompt maestro de la BD y el contexto dinámico del grafo.
    """
    llm = get_llm()
    lineas_historial = []
    
    # Formateamos historial reciente para que Ali no pierda el hilo
    for m in mensajes[-6:-1]:
        txt = getattr(m, 'content', str(m))
        lineas_historial.append(f"- {txt}")
    
    historial_str = "\n".join(lineas_historial) if lineas_historial else "Inicio de la charla."
    nombre_seguro = nombre or "cliente"
    
    # =================================================================
    # 1. TRAER EL PROMPT MAESTRO DESDE LA BASE DE DATOS
    # =================================================================
    prompt_maestro_bd = obtener_prompt_agente_sync()

    # =================================================================
    # 2. FUSIÓN: PROMPT MAESTRO + INSTRUCCIONES DEL GRAFO
    # =================================================================
    # Inyectamos el contexto de negocio, nombre e historial al final del prompt de la BD
    system_template = (
        f"{prompt_maestro_bd}\n\n"
        "--- INSTRUCCIONES DE ESTADO ACTUAL ---\n"
        "Estás hablando con el cliente: {nombre}.\n\n"
        "🎯 INSTRUCCIÓN ESTRICTA PARA ESTE MENSAJE (CONTEXTO):\n"
        "{contexto}\n\n"
        "HISTORIAL RECIENTE:\n"
        "{historial}"
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_template),
        ("human", "{texto}")
    ])
    
    # Ejecutamos la cadena
    res = (prompt | llm).invoke({
        "nombre": nombre_seguro, 
        "historial": historial_str, 
        "contexto": contexto_proceso,
        "texto": ultimo_usuario
    })
    
    # Limpiamos exceso de espacios y quitamos los asteriscos de markdown
    respuesta_final = res.content.replace("*", "").strip()
    return respuesta_final

__all__ = ["generar_respuesta_contextual"]