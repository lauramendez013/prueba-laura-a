# app/utils/messages.py

from typing import Any, List


def ensure_list_messages(msgs: Any) -> List[Any]:
    """
    Normaliza el campo `messages` del estado a una lista.

    Acepta:
      - None
      - un string
      - un solo mensaje (objeto)
      - una lista de mensajes

    Retorna SIEMPRE una lista.
    """
    if msgs is None:
        return []
    if isinstance(msgs, list):
        return msgs
    return [msgs]


def get_last_text(msgs: Any) -> str:
    """
    Devuelve el texto del ÚLTIMO mensaje en `msgs`.

    Soporta:
      - strings
      - objetos con atributo .content (p.ej. mensajes de LangChain)
      - dicts con clave "content"
      - cualquier otro objeto → se castea con str(...)
    """
    lst = ensure_list_messages(msgs)
    if not lst:
        return ""

    last = lst[-1]

    # Caso string plano
    if isinstance(last, str):
        return last

    # Caso objeto con atributo .content (LangChain, etc.)
    content = getattr(last, "content", None)
    if content is not None:
        # Si el content no es string, lo convertimos
        return content if isinstance(content, str) else str(content)

    # Caso dict con "content"
    if isinstance(last, dict) and "content" in last:
        return str(last["content"])

    # Fallback genérico
    return str(last)
