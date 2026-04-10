# app/main.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
import asyncio
from datetime import datetime, timedelta
import os
import logging
import re
from collections import defaultdict # 🌟 AGREGADO PARA EL CANDADO INDIVIDUAL

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
from app.core.database import init_db, get_session
from app.core.models.rule import Rule, RuleCreate, RuleRead
from app.core.models.agente import AgenteCreate, AgenteRead
from app.services.rule_service import RuleService
from app.services.agente_service import AgenteService

from app.tools.zoho_roles import list_contact_roles
from langchain_core.messages import HumanMessage, AIMessage

# ==================================================
# LOGS
# ==================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ALI_API")

# ==================================================
# INIT
# ==================================================

load_dotenv()
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Al arrancar la app: Crea las tablas
    await init_db()
    yield
    # Al apagar la app: (Opcional) cerrar conexiones limpias

api = FastAPI(title="Coninsa Multiagente API", version="1.0.0",lifespan=lifespan)

@api.middleware("http")
async def log_raw_requests(request: Request, call_next):
    if request.method == "POST":
        body = await request.body()
        logger.info(f"RAW PAYLOAD RECIBIDO: {body.decode(errors='ignore')}")
        
        async def receive():
            return {"type": "http.request", "body": body}
        request._receive = receive
        
    return await call_next(request)

_allowed = os.getenv("AllowedOrigins", "*").strip()
allow_origins = (
    ["*"]
    if _allowed == "*" or not _allowed
    else [o.strip() for o in _allowed.split(",") if o.strip()]
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================================================
# SESIONES Y CACHÉ ANTI-REBOTE
# ==================================================

class SessionData(BaseModel):
    state: Dict[str, Any]
    last_len: int = 0
    updated_at: datetime = Field(default_factory=datetime.utcnow)

_SESSIONS: Dict[str, SessionData] = {}
_SESSION_LOCKS = defaultdict(asyncio.Lock) # 🔥 EL CAMBIO: Un candado por cliente
_SESSION_TTL = timedelta(hours=24)

# 🔥 CACHÉ GLOBAL FUERA DE LA IA PARA QUE NO LO BORRE
_BOUNCE_CACHE: Dict[str, Dict[str, Any]] = {}


def _new_state() -> Dict[str, Any]:
    return {
        "messages": [],
        "operacion": "inicio",
        
        # NUEVO CAMPO: Para almacenar el JSON puro de la plantilla
        "plantilla_twilio": None,

        "datos_inmueble": {},
        "resultado": {},

        # IDENTIDAD
        "user_id": None,
        "sender_id": None,
        "user_phone": None,
        "session_id": None,

        # Política
        "politica_mostrada": False,
        "politica_aceptada": False,
        "politica_rechazada_previa": False,

        # CRM / VALIDACIÓN
        "cliente_existente": False,
        "identidad_completa": False,
        "telefono_validado": False,

        # Flujo
        "modo": None,
        "proposito": None,
        "proposito_usuario": None,

        # Identidad
        "confirmando_nombre": False,
        "confirmando_celular": False,
        "esperando_nombre": False,
        "esperando_celular": False,

        # Registro
        "solicitud_registro_enviada": False,

        # Propósito
        "preguntando_proposito": False,
        "confirmando_proposito": False,
    }


def _prune_expired() -> None:
    now = datetime.utcnow()
    for sid in [
        k for k, v in _SESSIONS.items()
        if v.updated_at + _SESSION_TTL < now
    ]:
        _SESSIONS.pop(sid, None)

# ==================================================
# MODELOS I/O
# ==================================================

class ChatInputPayload(BaseModel):
    session_id: str = Field(default="test_session_123", description="ID de la sesión")
    message: str = Field(default="Hola, quiero información", description="Mensaje del usuario")
    user: Dict[str, Any] = Field(default={"phone": "3001234567"}, description="Datos del usuario")
    chats: List[Dict[str, Any]] = Field(default=[], description="Historial previo de Daxia")

class ChatResponse(BaseModel):
    replies: List[str]
    answer: str
    plantilla_twilio: Optional[Dict[str, Any]] = None 


class ResetRequest(BaseModel):
    session_id: str


class StateResponse(BaseModel):
    session_id: str
    state: Dict[str, Any]

# ==================================================
# HELPERS
# ==================================================

E164 = re.compile(r"^\+?\d{7,15}$")


def _normalize_phone(v: str) -> str | None:
    if not v:
        return None

    p = re.sub(r"\D+", "", v)

    if len(p) == 10 and not p.startswith("57"):
        p = "57" + p

    if not p.startswith("+"):
        p = "+" + p

    if not E164.match(p):
        return None

    return p


def _extract_user_phone(data: Dict[str, Any]) -> str | None:
    user = data.get("user")
    if isinstance(user, dict):
        phone = user.get("phone")
        if phone:
            return _normalize_phone(str(phone))
    return None


def _ensure_list_messages(msgs: Any) -> List[Any]:
    if msgs is None:
        return []
    return msgs if isinstance(msgs, list) else [msgs]


def _msg_text(m: Any) -> str:
    if m is None:
        return ""
    if isinstance(m, str):
        return m
    if isinstance(m, dict):
        c = m.get("content")
        if isinstance(c, str):
            return c
    content = getattr(m, "content", None)
    if isinstance(content, str):
        return content
    return str(m)


def _diff_messages(before: List[Any], after: List[Any]) -> List[str]:
    new = after[len(before):]
    out: List[str] = []
    for x in new:
        t = (_msg_text(x) or "").strip()
        if t:
            out.append(t)
    return out

# ==================================================
# 🔥 LÓGICA DE BÚSQUEDA EN SEGUNDO PLANO 🔥
# ==================================================
async def ejecutar_pre_busqueda_pesada(session_id: str):
    """
    Lanza la búsqueda y evaluación pesada en segundo plano mientras
    el cliente lee y responde la pregunta puente (codeudor o pago).
    """
    from app.workflow import app as graph_app
    
    sess = _SESSIONS.get(session_id)
    if not sess: return

    logger.info(f"⚙️ Iniciando Búsqueda Pesada en fondo para sesión: {session_id}")
    
    # Creamos una copia del estado para no ensuciar el historial principal
    estado_fondo = sess.state.copy()
    
    # Inyectamos una bandera secreta. El Router verá esto y mandará 
    # el flujo directo al Ejecutor para que busque en silencio.
    datos_fondo = dict(estado_fondo.get("datos_inmueble", {}))
    datos_fondo["busqueda_en_fondo_activa"] = True
    estado_fondo["datos_inmueble"] = datos_fondo
    
    try:
        # Esto corre la API de Coninsa + Evaluación del LLM sin bloquear a nadie
        resultado_fondo = await asyncio.to_thread(graph_app.invoke, estado_fondo)
        
        # 🧳 GUARDAMOS EN LA MALETA:
        # Extraemos los inmuebles encontrados y los guardamos en la sesión REAL,
        # así cuando el usuario diga "Sí me quedó claro", ya estarán listos.
        if session_id in _SESSIONS:
            inmuebles_listos = resultado_fondo["datos_inmueble"].get("ultimos_inmuebles", [])
            _SESSIONS[session_id].state["datos_inmueble"]["inmuebles_precalculados"] = inmuebles_listos
            logger.info(f"✅ Búsqueda de fondo terminada. {len(inmuebles_listos)} resultados guardados en la maleta.")
            
    except Exception as e:
        logger.error(f"❌ Error en búsqueda de fondo: {e}")

# ==================================================
# ENDPOINT /chat
# ==================================================

# 🔥 LE QUITAMOS EL response_model=ChatResponse para permitir respuestas flexibles
@api.post("/chat")
async def chat(payload: ChatInputPayload, background_tasks: BackgroundTasks): # 👈 AGREGAMOS BACKGROUND TASKS
    from app.workflow import app as graph_app

    data = payload.model_dump() if hasattr(payload, 'model_dump') else payload.dict()

    session_id = str(data.get("session_id") or "")
    message = str(data.get("message") or "")
    user_phone = _extract_user_phone(data)

    logger.info(
        f"INCOMING | session_id={session_id} | phone={user_phone} | message={message}"
    )

    # 🔥 EL CAMBIO: Usamos el candado específico de este usuario
    session_lock = _SESSION_LOCKS[session_id]
    
    async with session_lock:
        _prune_expired()

        sess = _SESSIONS.get(session_id)

        # 1. SI ES UNA SESIÓN NUEVA
        if not sess:
            state = _new_state()
            state["user_id"] = user_phone
            state["sender_id"] = user_phone
            state["user_phone"] = user_phone
            state["session_id"] = session_id
            
            historial_langchain = []
            chats_daxia = data.get("chats", [])
            
            for c in chats_daxia:
                tipo = c.get("sender_type")
                texto = c.get("content", {}).get("text", "")
                
                if not texto:
                    continue
                    
                if tipo == "user":
                    historial_langchain.append(HumanMessage(content=texto))
                elif tipo == "ia":
                    historial_langchain.append(AIMessage(content=texto))
            
            state["messages"] = historial_langchain
            
            sess = SessionData(state=state)
            _SESSIONS[session_id] = sess
        else:
            sess.state["user_id"] = user_phone
            sess.state["sender_id"] = user_phone
            sess.state["user_phone"] = user_phone
            sess.state["session_id"] = session_id

        # =================================================================
        # 2. ESCUDO ANTI-REBOTES (LA TÁCTICA DEL SECUESTRO) 🛡️
        # =================================================================
        mensaje_limpio = message.strip()
        ahora = datetime.utcnow()
        
        bounce_info = _BOUNCE_CACHE.get(session_id, {})
        ultimo_msg = bounce_info.get("text", "")
        ultimo_tiempo = bounce_info.get("time", datetime.min)
        
        tiempo_pasado = (ahora - ultimo_tiempo).total_seconds()
        
        # 🛑 EL CAMBIO: Ampliamos la paciencia a 90 segundos. Si Daxia llora en menos de 1 minuto, lo ignoramos.
        if mensaje_limpio == ultimo_msg and tiempo_pasado < 90:
            logger.warning(f"REBOTE DETECTADO: '{mensaje_limpio}'. Ali sigue buscando. Secuestrando petición.")
            # Le mandamos un timeout intencional a Daxia para que no estorbe el proceso original
            raise HTTPException(status_code=408, detail="Timeout forzado para ignorar rebote")
            
        _BOUNCE_CACHE[session_id] = {"text": mensaje_limpio, "time": ahora}

        if len(_BOUNCE_CACHE) > 1000:
            _BOUNCE_CACHE.clear()

        # =================================================================
        # 3. PROCESAMIENTO NORMAL DE IA
        # =================================================================
        before_msgs = _ensure_list_messages(sess.state.get("messages"))
        
        sess.state["messages"] = before_msgs + [HumanMessage(content=mensaje_limpio)]

        # Limpiamos el objeto plantilla del turno anterior para que no se envíe doble
        sess.state["plantilla_twilio"] = None

        try:
            new_state = graph_app.invoke(sess.state)
        except Exception as e:
            logger.error(f"Error al invocar el flujo de IA: {str(e)}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"Error en el servicio de IA: {str(e)}")

        sess.state = new_state
        sess.updated_at = ahora 

        after_msgs = _ensure_list_messages(new_state.get("messages"))
        
        replies = _diff_messages(before_msgs + [HumanMessage(content=mensaje_limpio)], after_msgs)

        # =================================================================
        # 4. ARMADO DE LA RESPUESTA FINAL PARA DAXIA
        # =================================================================
        texto_respuesta = replies[-1] if replies else ""
        plantilla = new_state.get("plantilla_twilio")

        # 🔥 EL "TRUCO" DEL PUENTE (DISPARADOR DE BÚSQUEDA) 🔥
        if new_state.get("operacion") == "busqueda_validar":
            logger.info("🌉 Puente detectado: Disparando búsqueda pesada en segundo plano...")
            background_tasks.add_task(ejecutar_pre_busqueda_pesada, session_id)

        # 🔥 CASO ESPECIAL: Múltiples Plantillas (Daxia lo exige en la raíz)
        if plantilla and plantilla.get("type") == "multiple_templates":
            return {
                "type": "multiple_templates",
                "answer": texto_respuesta,
                "templates": plantilla.get("templates", [])
            }
            
        # 🔥 CASO NORMAL: Solo texto o 1 sola plantilla 
        return {
            "replies": replies or [""],
            "answer": texto_respuesta,
            "plantilla_twilio": plantilla
        }

# ==================================================
# ENDPOINTS AUXILIARES
# ==================================================

@api.post("/reset")
async def reset_session(req: ResetRequest):
    # En este caso particular, podríamos usar el candado global solo para limpiar, 
    # pero como cambiamos a defaultdict, es más seguro simplemente eliminar la llave de memoria.
    _SESSIONS.pop(req.session_id, None)
    return {"session_id": req.session_id, "reset": True}


@api.get("/state", response_model=StateResponse)
async def get_state(session_id: str):
    sess = _SESSIONS.get(session_id)
    return StateResponse(
        session_id=session_id,
        state=(sess.state if sess else _new_state()),
    )

@api.post("/rules", response_model=RuleRead)
async def create_rule(rule: RuleCreate, service: RuleService = Depends(RuleService)):
    return await service.create(rule)


@api.post("/agente", response_model=AgenteRead)
async def upsert_agente(agente: AgenteCreate, service: AgenteService = Depends(AgenteService)):
    """Crea un agente o lo actualiza si la key ya existe."""
    return await service.upsert(agente)