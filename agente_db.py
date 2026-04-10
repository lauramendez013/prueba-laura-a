# app/tools/agente_db.py
import asyncio
import time
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.database.connection import engine as global_engine
from app.core.models.agente import Agente

# ==========================================
# 🧠 CACHÉ EN MEMORIA PARA VELOCIDAD Y ESTABILIDAD
# ==========================================
_CACHE_PROMPT = {}
_TTL_SEGUNDOS = 60  # Guarda el prompt en memoria por 1 minuto

def obtener_prompt_agente_sync() -> str:
    key_agente = "daxia-agente-14"
    ahora = time.time()

    # 1. Revisamos el caché primero (Evita el WinError 1225 y responde al instante)
    if key_agente in _CACHE_PROMPT:
        prompt_guardado, timestamp = _CACHE_PROMPT[key_agente]
        if ahora - timestamp < _TTL_SEGUNDOS:
            return prompt_guardado

    # 2. Si no está en caché o ya expiró, vamos a la BD (El proceso lento)
    async def _fetch():
        temp_engine = create_async_engine(global_engine.url)
        try:
            async with AsyncSession(temp_engine) as session:
                query = select(Agente).where(Agente.key == key_agente)
                result = await session.execute(query)
                agente_db = result.scalars().first()
                
                if agente_db and agente_db.prompt:
                    return agente_db.prompt
        except Exception as e:
            print(f"Error interno SQL al buscar agente: {e}")
        finally:
            await temp_engine.dispose()
            
        return ""

    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio
        nest_asyncio.apply()
        nuevo_prompt = loop.run_until_complete(_fetch())
    except RuntimeError:
        nuevo_prompt = asyncio.run(_fetch())

    # 3. Guardamos el resultado exitoso en el caché 💾
    if nuevo_prompt:
        _CACHE_PROMPT[key_agente] = (nuevo_prompt, ahora)
    elif key_agente in _CACHE_PROMPT:
        # 🔥 SALVAVIDAS: Si la BD falla (WinError 1225) pero tenemos un prompt viejo en memoria, usamos el viejo por seguridad
        return _CACHE_PROMPT[key_agente][0]

    return nuevo_prompt