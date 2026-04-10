# app/agents/politica.py

from app.state import InmuebleState
from app.utils.intent import clasificar_politica
from app.utils.messages import ensure_list_messages

POLICY_URL = (
    "https://www.coninsa.co/"
    "politica-de-tratamiento-de-datos-personales-de-coninsa-ramon-h-sa"
)


def _texto(m) -> str:
    """
    Extrae texto de:
    - str
    - HumanMessage / AIMessage (content)
    - dict {content: "..."}
    - fallback str(obj)
    """
    if m is None:
        return ""
    if isinstance(m, str):
        return m
    content = getattr(m, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(m, dict):
        c = m.get("content")
        if isinstance(c, str):
            return c
    return str(m)


def _ultimo_texto_usuario(msgs) -> str:
    """
    Devuelve el último mensaje REAL del usuario (normalizado).
    Ignora mensajes del bot relacionados con la política.
    """
    for m in reversed(msgs or []):
        t = (_texto(m) or "").strip()
        if not t:
            continue

        low = t.lower()
        if "política de tratamiento de datos" in low:
            continue
        if "¿aceptas la política" in low:
            continue
        if "si cambias de opinión" in low:
            continue
        if "comprendo totalmente tu decisión" in low:
            continue

        return low.strip()

    return ""


def agente_politica(state: InmuebleState):
    mensajes = ensure_list_messages(state.get("messages"))
    datos = dict(state.get("datos_inmueble", {}) or {})

    # -------------------------------------------------
    # FAST PATH: política ya aceptada
    # -------------------------------------------------
    if state.get("politica_aceptada"):
        return {
            **state,
            "messages": mensajes,
            "datos_inmueble": datos,
            "next_agent": "router",
        }

    ultimo_usuario = _ultimo_texto_usuario(mensajes)

    # -------------------------------------------------
    # 1) Mostrar política por primera vez (PLANTILLA TWILIO)
    # -------------------------------------------------
    if not state.get("politica_mostrada"):
        plantilla = {
            "type": "template", 
            "template_id": "HX112fceba9a6e56d2dc89f74327220301",
            "variables": {}
        }
        
        return {
            **state,
            "plantilla_twilio": plantilla, # 🔥 Se envía el objeto independiente solo la primera vez
            "politica_mostrada": True,
            "politica_aceptada": False,
            "next_agent": "__end__",
        }

    # -------------------------------------------------
    # 2) FAST PATH DETERMINÍSTICO (sin LLM)
    # -------------------------------------------------
    low = (ultimo_usuario or "").lower().strip()

    # Validaciones EXACTAS
    aceptaciones_cortas = [
        "si", "sí", "sip", "sii", "siii", "aja", "ajá", "okey", "ok",
        "claro", "dale", "listo", "bueno", "obvio", "yes", "s"
    ]
    rechazos_cortos = ["no", "nop", "nopo", "nunca", "jamas", "jamás", "n"]

    # Validaciones por FRAGMENTOS (frases completas)
    aceptaciones_largas = [
        "si acepto", "sí acepto", "acepto", "de acuerdo", 
        "ok acepto", "está bien", "esta bien", "claro que si", "claro que sí"
    ]
    rechazos_largos = [
        "no acepto", "no quiero", "rechazo", "no acepto gracias",
        "no me gustaria", "no me gustaría", "no autorizo", "no de acuerdo"
    ]

    # Mensaje de rechazo natural y firme (sin obligar a responder de cierta manera)
    msg_rechazo = (
        "Comprendo totalmente tu decisión. Sin embargo, por lineamientos legales y de seguridad, "
        "para poder continuar ayudándote es un requisito indispensable que aceptes nuestra Política de Tratamiento de Datos.\n\n"
        f"Puedes revisarla a detalle aquí: {POLICY_URL}"
    )

    # 🚨 LA CORRECCIÓN: EVALUAR EL RECHAZO PRIMERO 🚨
    if low in rechazos_cortos or any(x in low for x in rechazos_largos):
        return {
            **state,
            "messages": mensajes + [msg_rechazo],
            "politica_aceptada": False,
            "politica_rechazada_previa": True,
            "next_agent": "__end__",
        }

    # SI NO HUBO RECHAZO, AHORA SÍ EVALÚA SI ACEPTÓ
    if low in aceptaciones_cortas or any(x in low for x in aceptaciones_largas):
        return {
            **state,
            "messages": mensajes,
            "politica_aceptada": True,
            "politica_mostrada": True,
            "politica_rechazada_previa": False,
            "datos_inmueble": datos,
            "next_agent": "bienvenida",
        }

    # -------------------------------------------------
    # 3) LLM COMO RESPALDO (casos realmente ambiguos)
    # -------------------------------------------------
    contexto = "reconfirmacion" if state.get("politica_rechazada_previa") else ""

    try:
        out = clasificar_politica(ultimo_usuario, contexto=contexto)
        print("[DEBUG politica] texto:", ultimo_usuario)
        print("[DEBUG politica] out:", out)
    except Exception as e:
        print("[ERROR agente_politica] clasificar_politica:", repr(e))
        out = None

    if out and out.decision == "rechaza":
        return {
            **state,
            "messages": mensajes + [msg_rechazo],
            "politica_aceptada": False,
            "politica_rechazada_previa": True,
            "next_agent": "__end__",
        }

    if out and out.decision == "acepta":
        return {
            **state,
            "messages": mensajes,
            "politica_aceptada": True,
            "politica_mostrada": True,
            "politica_rechazada_previa": False,
            "datos_inmueble": datos,
            "next_agent": "bienvenida",
        }

    if out and out.decision == "pregunta":
        msg = (
            "Para continuar, es necesario que aceptes la política de tratamiento de datos.\n"
            f"Puedes revisarla aquí: {POLICY_URL}"
        )
        return {
            **state,
            "messages": mensajes + [msg],
            "next_agent": "__end__",
        }

    # -------------------------------------------------
    # 4) Ambigüedad real
    # -------------------------------------------------
    msg = (
        "No me quedó claro si aceptas la política de tratamiento de datos.\n"
        "Para poder continuar es necesario que nos confirmes si la aceptas."
    )
    return {
        **state,
        "messages": mensajes + [msg],
        "politica_aceptada": False,
        "next_agent": "__end__",
    }

__all__ = ["agente_politica"]