# app/tools/reglas_db.py
import asyncio
import time
import logging
from typing import Optional
import unicodedata
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.database.connection import engine as global_engine
from app.core.models.rule import Rule

logger = logging.getLogger("REGLAS_DB")

# ==========================================
# 🧠 CACHÉ EN MEMORIA PARA VELOCIDAD
# ==========================================
_CACHE_TICKET = {}
_CACHE_CIUDADES = {}
_TTL_SEGUNDOS = 300  # Guardamos las reglas por 5 minutos

def quitar_tildes_db(texto: str) -> str:
    if not texto: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn').lower().strip()

def obtener_ticket_minimo_sync(operacion: str, ciudad_raw: str) -> Optional[int]:
    op_limpia = "arriendo" if "arriendo" in (operacion or "").lower() else "venta"
    ciudad_limpia = quitar_tildes_db(ciudad_raw or "").split('/')[0]
    
    cache_key = f"{op_limpia}_{ciudad_limpia}"
    ahora = time.time()

    # 1. Revisar caché
    if cache_key in _CACHE_TICKET:
        valor, timestamp = _CACHE_TICKET[cache_key]
        if ahora - timestamp < _TTL_SEGUNDOS:
            logger.info(f"⚡ [CACHÉ] Ticket mínimo recuperado al instante para: {cache_key}")
            return valor

    # 2. Si no hay caché, ir a la BD
    logger.info(f"🔍 [BD] Consultando base de datos para ticket mínimo de: {cache_key}...")
    async def _fetch():
        temp_engine = create_async_engine(global_engine.url)
        try:
            async with AsyncSession(temp_engine) as session:
                query = select(Rule)
                result = await session.execute(query)
                reglas = result.scalars().all()
                for regla in reglas:
                    condiciones = regla.conditions or {}
                    c_ciudad = quitar_tildes_db(condiciones.get("ciudad", ""))
                    c_operacion = quitar_tildes_db(condiciones.get("operacion", ""))
                    if c_ciudad == ciudad_limpia and c_operacion == op_limpia:
                        if regla.value:
                            return int(regla.value)
        except Exception as e:
            logger.error(f"❌ Error SQL al buscar ticket: {e}")
        finally:
            await temp_engine.dispose()
        return None

    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio
        nest_asyncio.apply()
        nuevo_valor = loop.run_until_complete(_fetch())
    except RuntimeError:
        nuevo_valor = asyncio.run(_fetch())

    # 3. Guardar en caché
    if nuevo_valor is not None:
        logger.info(f"✅ [BD] Ticket encontrado y guardado en caché: {nuevo_valor}")
        _CACHE_TICKET[cache_key] = (nuevo_valor, ahora)
    elif cache_key in _CACHE_TICKET:
        logger.warning(f"⚠️ [BD] Falló la BD, usando ticket de rescate en caché para: {cache_key}")
        return _CACHE_TICKET[cache_key][0]

    return nuevo_valor

def obtener_ciudades_cobertura_sync() -> str:
    cache_key = "cobertura_global"
    ahora = time.time()

    # 1. Revisar caché
    if cache_key in _CACHE_CIUDADES:
        valor, timestamp = _CACHE_CIUDADES[cache_key]
        if ahora - timestamp < _TTL_SEGUNDOS:
            logger.info("⚡ [CACHÉ] Ciudades de cobertura recuperadas al instante.")
            return valor

    # 2. Si no hay caché, ir a la BD
    logger.info("🔍 [BD] Consultando base de datos para ciudades de cobertura...")
    async def _fetch_cities():
        temp_engine = create_async_engine(global_engine.url)
        try:
            async with AsyncSession(temp_engine) as session:
                ciudades_unicas = set()
                query = select(Rule)
                result = await session.execute(query)
                reglas = result.scalars().all()
                for regla in reglas:
                    condiciones = regla.conditions or {}
                    ciudad_db = condiciones.get("ciudad")
                    if ciudad_db:
                        ciudades_unicas.add(ciudad_db.strip().title())
                
                if ciudades_unicas:
                    return ", ".join(sorted(list(ciudades_unicas)))
        except Exception as e:
            logger.error(f"❌ Error SQL al buscar ciudades: {e}")
        finally:
            await temp_engine.dispose()
        return "Bogotá, Medellín, Barranquilla"

    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio
        nest_asyncio.apply()
        nuevas_ciudades = loop.run_until_complete(_fetch_cities())
    except RuntimeError:
        nuevas_ciudades = asyncio.run(_fetch_cities())

    # 3. Guardar en caché
    if nuevas_ciudades:
        logger.info(f"✅ [BD] Ciudades encontradas y guardadas en caché: {nuevas_ciudades}")
        _CACHE_CIUDADES[cache_key] = (nuevas_ciudades, ahora)

    return nuevas_ciudades