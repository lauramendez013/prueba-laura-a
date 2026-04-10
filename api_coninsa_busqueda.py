# app/tools/api_coninsa_busqueda.py
import requests
import logging
import asyncio
import aiohttp
import math

logger = logging.getLogger(__name__)

# Base URLs de la API de Coninsa
BASE_URL_V2 = "https://api.coninsa.co/api/v2"

def calcular_distancia_km(lat1, lon1, lat2, lon2):
    """
    Calcula la distancia en kilómetros entre dos coordenadas GPS 
    usando la fórmula de Haversine.
    """
    R = 6371.0 # Radio de la Tierra en km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

async def _fetch_inmueble_detalle(session, url_det, cod, semaforo):
    """Descarga el detalle de un inmueble, protegido por semáforo."""
    async with semaforo:
        await asyncio.sleep(0.2) 
        try:
            async with session.get(url_det, timeout=10) as response:
                if response.status == 200:
                    det_data = await response.json()
                    return det_data[0] if isinstance(det_data, list) and len(det_data) > 0 else det_data
                return None
        except Exception as e_det:
            logger.error(f"[API CONINSA] ❌ Error obteniendo detalle del código {cod}: {e_det}")
            return None

async def _obtener_detalles_concurrentes(codigos: list, servicio: str) -> list:
    """Lanza las peticiones en paralelo controladas a máximo 4 a la vez."""
    semaforo = asyncio.Semaphore(4)
    connector = aiohttp.TCPConnector(limit_per_host=4)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        tareas = []
        for cod in codigos:
            url_det = f"{BASE_URL_V2}/inmuebles-arriendo/{cod}" if servicio == "AR" else f"{BASE_URL_V2}/inmuebles-venta/{cod}"
            tarea = asyncio.create_task(_fetch_inmueble_detalle(session, url_det, cod, semaforo))
            tareas.append(tarea)
            
        resultados = await asyncio.gather(*tareas)
        return [res for res in resultados if res is not None]

def buscar_inmuebles_coninsa(filtros: dict) -> list:
    """Ejecuta la búsqueda dinámica y prioriza por distancia real en KM."""
    servicio = filtros.get("Servicio", "AR")
    barrio_objetivo = str(filtros.get("Barrio", "")).lower()
    
    lat_origen = filtros.get("latitud")
    lon_origen = filtros.get("longitud")
    
    payload_dinamico = {
        "servicio": servicio,
        "filters": [{"field": "tipo_inmueble", "operator": "LIKE", "value": "Apartamento"}],
        "ranges": [],
        "limit": 30,
        "offset": 0
    }
    
    if filtros.get("Ciudad"):
        payload_dinamico["filters"].append({"field": "ciudad", "operator": "LIKE", "value": filtros["Ciudad"]})
        
    # 🔥 RADIO ESTRICTO REDUCIDO: 0.02 grados son aprox 2.2 km.
    if lat_origen and lon_origen:
        radio = 0.03 
        payload_dinamico["geo_box"] = {
            "lat_min": float(lat_origen) - radio, "lat_max": float(lat_origen) + radio,
            "lon_min": float(lon_origen) - radio, "lon_max": float(lon_origen) + radio
        }
        logger.info(f"📍 Búsqueda por GEO_BOX ajustada (Radio ~2.2km) para: {lat_origen}, {lon_origen}")

    if filtros.get("Habitacion"):
        payload_dinamico["filters"].append({"field": "alcobas", "operator": "GREATER_THAN_OR_EQUAL", "value": int(filtros["Habitacion"])})
    
    if filtros.get("Banos"):
        payload_dinamico["filters"].append({"field": "banos", "operator": "GREATER_THAN_OR_EQUAL", "value": int(filtros["Banos"])})
            
    if filtros.get("ValorHasta"):
        campo_valor = "valor_arr" if servicio == "AR" else "valor_venta"
        payload_dinamico["ranges"].append({"field": campo_valor, "min": 0, "max": int(filtros["ValorHasta"])})

    try:
        # 1. Traer códigos
        res_busqueda = requests.post(f"{BASE_URL_V2}/inmuebles-busqueda-dinamica", json=payload_dinamico, timeout=10)
        res_busqueda.raise_for_status()
        codigos = res_busqueda.json().get("codes", [])
        
        if not codigos:
            return []

        # 2. Traer detalles
        try: loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        inmuebles_detallados = loop.run_until_complete(_obtener_detalles_concurrentes(codigos, servicio))
            
        # ==================================================
        # 3. FILTRO Y ORDENAMIENTO POR DISTANCIA REAL (KM) 🔥
        # ==================================================
        inmuebles_filtrados = []
        
        if lat_origen and lon_origen:
            lat_o = float(lat_origen)
            lon_o = float(lon_origen)
            
            for inm in inmuebles_detallados:
                ubic = inm.get("fieldLatLong", {})
                if ubic and ubic.get("lat") and ubic.get("lon"):
                    lat_i = float(ubic["lat"])
                    lon_i = float(ubic["lon"])
                    distancia = calcular_distancia_km(lat_o, lon_o, lat_i, lon_i)
                    
                    # Guardamos la distancia en el objeto para poder ordenarlos
                    inm["_distancia_km"] = distancia
                    
                    # 🔥 DESCARTAR LOS QUE ESTÉN A MÁS DE 3.5 KM REALES
                    if distancia <= 3.5:
                        inmuebles_filtrados.append(inm)
                else:
                    # Si no tiene coordenadas, lo dejamos pasar pero con prioridad muy baja
                    inm["_distancia_km"] = 99.0
                    inmuebles_filtrados.append(inm)
            
            # Ordenamos: Los más cercanos primero (los de 0.5km, 1km... etc)
            inmuebles_filtrados.sort(key=lambda x: x.get("_distancia_km", 99.0))
            logger.info("✅ Inmuebles ordenados matemáticamente por distancia real en KM.")

        elif barrio_objetivo and barrio_objetivo != "none":
            # Si no hay GPS, ordenamos por coincidencia exacta del nombre del barrio
            def afinidad_barrio(inm):
                return 0 if barrio_objetivo in str(inm).lower() else 1
            
            inmuebles_detallados.sort(key=afinidad_barrio)
            inmuebles_filtrados = inmuebles_detallados
            logger.info(f"✅ Priorización por texto aplicada para el sector: {barrio_objetivo}")
        else:
            inmuebles_filtrados = inmuebles_detallados

        return inmuebles_filtrados
        
    except Exception as e:
        logger.error(f"❌ Error crítico en el motor de búsqueda Coninsa: {e}")
        return []