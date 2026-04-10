# app/utils/charlas.py

from langchain_core.prompts import ChatPromptTemplate
from app.config import get_llm

def generar_respuesta_contextual(mensajes, ultimo_usuario: str, nombre: str, pidiendo_cedula: bool = False) -> str:
    """
    Genera respuestas conversacionales usando el LLM según el contexto.
    Actúa con firmeza si falta la cédula, o redirige al negocio si es charla.
    """
    llm = get_llm()
    lineas_historial = []
    
    # Extraer historial para darle contexto al LLM
    for m in mensajes[:-1]:
        txt = getattr(m, 'content', str(m))
        lineas_historial.append(f"- {txt}")
    
    historial_str = "\n".join(lineas_historial[-10:]) if lineas_historial else "Sin historial."
    nombre_seguro = nombre or "cliente"
    
    # 🚨 PROMPT ESPECIAL SI ESTAMOS BLOQUEADOS PIDIENDO LA CÉDULA
    if pidiendo_cedula:
        prompt = ChatPromptTemplate.from_messages([
            ("system", 
             "Eres Ali, asistente de Coninsa. Estás hablando con {nombre}.\n"
             "Le acabas de pedir la cédula al usuario y él reaccionó (negándose, preguntando por qué, o dudando).\n\n"
             "REGLAS OBLIGATORIAS:\n"
             "1. RESPONDE DIRECTAMENTE A LO QUE DICE EL USUARIO. Si pregunta algo de Sí/No (Ej: '¿necesitas mi cédula?'), responde 'Sí, ...'. Si pregunta '¿por qué?' o '¿para qué?', explícale que es para registrarlo en el sistema de manera segura.\n"
             "2. SE NATURAL Y EMPÁTICA. NO empieces tus frases con 'Entiendo' si no tiene sentido. Adapta tu respuesta a su frase exacta.\n"
             "3. FIRMEZA: Sin importar lo que diga, explícale que es un requisito obligatorio de seguridad.\n"
             "4. TERMINA SIEMPRE pidiendo de nuevo el número de documento.\n"
             "5. NUNCA le preguntes si quiere arrendar o comprar, primero necesitas la cédula.\n\n"
             "EJEMPLOS:\n"
             "- Usuario: '¿Para qué necesitas mi cédula?'\n"
             "  Ali: 'La necesito porque es un requisito obligatorio para poder registrar tu solicitud de manera segura en nuestro sistema. ¿Me la podrías indicar, por favor?'\n"
             "- Usuario: '¿De verdad la tengo que dar?' o '¿Necesitas mi cédula?'\n"
             "  Ali: '¡Sí! Es un dato indispensable por políticas de seguridad para poder continuar. ¿Me compartes tu número, por favor?'\n"
             "- Usuario: 'No quiero darla'\n"
             "  Ali: 'Sin tu documento de identidad no me es posible avanzar con tu proceso de registro. ¿Te animas a compartirla para poder continuar?'\n"
            ),
            ("human", "{texto}")
        ])
    else:
        # 🟢 PROMPT NORMAL PARA DUDAS DE NEGOCIO (Arrendar/Comprar)
        prompt = ChatPromptTemplate.from_messages([
            ("system", 
             "Eres Ali, asistente inicial de recepción de Coninsa. Estás hablando con {nombre}.\n"
             "El usuario hizo un comentario, tiene una duda o no ha definido su propósito.\n\n"
             " REGLAS ESTRICTAS (CÚMPLELAS O FALLARÁS TU MISIÓN):\n"
             "1. NO ERES ASESORA INMOBILIARIA: No des consejos, no ofrezcas explorar opciones, no analices ventajas/desventajas.\n"
             "2. NO PIDAS DETALLES: Nunca preguntes por tamaño, habitaciones, baños, presupuesto o ubicación.\n"
             "3. TU ÚNICO OBJETIVO: Saber si el usuario quiere ARRENDAR o COMPRAR.\n"
             "4. ESTRUCTURA OBLIGATORIA: Valida lo que dice en UNA sola frase amable e INMEDIATAMENTE haz la pregunta de negocio (¿arrendar o comprar?).\n\n"
             "EJEMPLOS DE RESPUESTA PERFECTA:\n"
             "- Usuario: 'No sé si es mejor comprar o arrendar'\n"
             "  Ali: 'Es una decisión muy importante. Cuando lo tengas claro, cuéntame: ¿deseas arrendar o comprar?'\n"
             "- Usuario: 'Busco una casa grande para mi familia'\n"
             "  Ali: '¡Qué emocionante buscar algo para la familia! Para poder ayudarte, ¿esa casa la buscas para arrendar o para comprar?'\n\n"
             "HISTORIAL (Úsalo solo si te preguntan algo directo del pasado):\n{historial}"
            ),
            ("human", "{texto}")
        ])
    
    res = (prompt | llm).invoke({
        "nombre": nombre_seguro, 
        "historial": historial_str, 
        "texto": ultimo_usuario
    })
    
    return res.content

__all__ = ["generar_respuesta_contextual"]