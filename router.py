# app/agents/router.py
import re
import os
import logging
import requests
from urllib.parse import unquote_plus, unquote
from langchain_core.messages import AIMessage, SystemMessage

from app.state import InmuebleState
from app.utils.intent import interpretar_identidad_y_proposito, extraer_datos_busqueda 
from app.utils.messages import ensure_list_messages, get_last_text
from app.utils.charlas import generar_respuesta_contextual

logger = logging.getLogger("ROUTER_AGENT")

# Jalamos la llave de Google desde el entorno (.env o GCP)
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

# ============================================================
# DETECTOR DE RECHAZO / CIERRE (LÓGICA PURA MEJORADA)
# ============================================================
def es_rechazo_definitivo(texto: str, fase: str = "") -> bool:
    """
    Detecta si el usuario quiere CERRAR la oportunidad.
    Si el mensaje tiene palabras de ajuste (habitaciones, sol, etc), NO es rechazo.
    """
    t = re.sub(r'[^a-záéíóúñ\s]', '', str(texto).lower()).strip()
    t = re.sub(r'\s+', ' ', t)
    
    # ❌ SI HAY PALABRAS DE AJUSTE, EL ROUTER DEBE IGNORAR EL "NO"
    terminos_ajuste = [
        "habitacion", "alcoba", "baño", "bano", "mascota", "piso", "sol", 
        "ascensor", "parqueadero", "balcon", "necesito", "mejor", 
        "quitalo", "cambia", "otra cosa", "sin ", "no quiero que tenga", 
        "estudio", "cocina", "vista"
    ]
    if any(x in t for x in terminos_ajuste):
        return False

    # ✅ CIERRE REAL: Despedidas o desinterés total
    despedidas = [
        "adios", "chao", "hasta luego", "salir", "ya no quiero mas", 
        "no me interesa continuar", "no me gusto ninguno", "ninguno me gusto", 
        "miremos en otra parte", "no mas", "nada mas", "cancelar", "finalizar",
        "no me interesa ninguno", "ya no quiero"
    ]
    if any(x in t for x in despedidas):
        return True

    # "No" seco sin nada más cierra, SALVO que estemos en la fase de ajustes
    if t in ["no", "nop", "ninguno", "no gracias", "asi esta bien", "deja asi"]:
        if fase == "mas_ajustes":
            return False # Un "no" aquí significa "no quiero más ajustes, avancemos"
        return True
        
    return False

# ============================================================
# 1. TRADUCTOR DE COORDENADAS A TEXTO (GOOGLE MAPS)
# ============================================================
def obtener_direccion_por_gps(lat: float, lon: float) -> str:
    """Traduce coordenadas matemáticas a una dirección legible usando Google Maps API"""
    if not GOOGLE_MAPS_API_KEY:
        logger.error("❌ Falta GOOGLE_MAPS_API_KEY en el entorno. No se puede buscar.")
        return ""

    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "latlng": f"{lat},{lon}",
            "key": GOOGLE_MAPS_API_KEY,
            "language": "es"
        }
        respuesta = requests.get(url, params=params, timeout=5)
        datos = respuesta.json()
        
        if datos.get("status") == "OK" and datos.get("results"):
            direccion_completa = datos["results"][0].get("formatted_address", "")
            logger.info(f"🗺️ Google Reverse Geocoding exitoso: {direccion_completa}")
            return direccion_completa.replace(", Colombia", "")
        else:
            logger.warning(f"⚠️ Google Maps falló. Status: {datos.get('status')}")
            
    except Exception as e:
        logger.error(f"❌ Error en reverse geocoding con Google: {e}")
        
    return ""

# ============================================================
# 2. TRADUCTOR DE TEXTO A COORDENADAS (GOOGLE MAPS)
# ============================================================
def obtener_gps_por_direccion(direccion: str) -> dict:
    """Convierte texto como 'Chapinero, Bogotá' a Latitud y Longitud y devuelve la dirección real"""
    if not GOOGLE_MAPS_API_KEY:
        logger.error("❌ Falta GOOGLE_MAPS_API_KEY en el entorno. No se puede buscar.")
        return None

    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": direccion,
            "key": GOOGLE_MAPS_API_KEY,
            "language": "es"
        }
        respuesta = requests.get(url, params=params, timeout=5)
        datos = respuesta.json()
        
        if datos.get("status") == "OK" and datos.get("results"):
            location = datos["results"][0]["geometry"]["location"]
            direccion_formateada = datos["results"][0].get("formatted_address", "")
            return {
                "latitud": float(location["lat"]),
                "longitud": float(location["lng"]),
                "direccion_formateada": direccion_formateada
            }
        else:
            logger.warning(f"⚠️ Google Maps no pudo traducir el texto. Status: {datos.get('status')}")
            
    except Exception as e:
        logger.error(f"❌ Error buscando coordenadas para texto con Google: {e}")
        
    return None

# ============================================================
# 3. DESENROLLADOR DE URLS CORTAS
# ============================================================
def desenrollar_url_corta(url: str) -> tuple[str, str]:
    """Obtiene la URL final interceptando la redirección para evitar CAPTCHAs en Google Cloud"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, allow_redirects=False, timeout=5)
        
        url_larga = url
        if response.status_code in [301, 302, 303, 307, 308]:
            url_larga = response.headers.get('Location', url)
            
        if "consent.google.com" in url_larga and "continue=" in url_larga:
            match = re.search(r"continue=([^&]+)", url_larga)
            if match:
                url_larga = unquote(match.group(1))

        logger.info(f"🔗 URL desenrollada: {url_larga}")
        
        html_content = ""
        try:
            res_html = requests.get(url_larga, headers=headers, allow_redirects=True, timeout=5)
            html_content = res_html.text
        except:
            pass 
            
        return url_larga, html_content
    except Exception as e:
        logger.error(f"❌ Error desenrollando URL: {e}")
        return url, ""

# ============================================================
# 4. FUNCIÓN PRINCIPAL DE EXTRACCIÓN (USO FORZADO DE GOOGLE API)
# ============================================================
def extraer_datos_de_url_maps(texto: str) -> dict:
    if "lat=" in texto and "lng=" in texto:
        patron_daxia = r"lat=(-?\d+\.\d+).*?lng=(-?\d+\.\d+)"
        match_daxia = re.search(patron_daxia, texto)
        
        direccion_texto = ""
        if "dirección:" in texto.lower():
            partes = re.split(r'dirección:', texto, flags=re.IGNORECASE)
            if len(partes) > 1:
                direccion_texto = partes[1].strip()

        if match_daxia:
            return {
                "latitud": float(match_daxia.group(1)), 
                "longitud": float(match_daxia.group(2)),
                "direccion_daxia": direccion_texto
            }

    urls_encontradas = []
    for palabra in texto.split():
        url_limpia = palabra.strip(",.;()[]")
        if ("goo.gl" in url_limpia.lower() or "google" in url_limpia.lower() or "maps" in url_limpia.lower()) and "/" in url_limpia:
            if not url_limpia.startswith("http"):
                url_limpia = "https://" + url_limpia
            urls_encontradas.append(url_limpia)

    for url in urls_encontradas:
        logger.info(f"🔗 Procesando link corto real: {url}")
        
        url_larga, html_content = desenrollar_url_corta(url)
            
        patrones_url = [
            r"!3d(-?\d{1,2}\.\d{3,})!4d(-?\d{1,3}\.\d{3,})",
            r"@(-?\d{1,2}\.\d{3,}),(-?\d{1,3}\.\d{3,})",
            r"(?:ll|center)=(-?\d{1,2}\.\d{3,})(?:,|%2C)(-?\d{1,3}\.\d{3,})"
        ]
        
        for patron in patrones_url:
            match = re.search(patron, url_larga)
            if match:
                return {"latitud": float(match.group(1)), "longitud": float(match.group(2))}

        lugar_texto = ""
        match_place = re.search(r"place/([^/]+)", url_larga)
        match_search = re.search(r"search/([^/]+)", url_larga)
        match_q = re.search(r"[?&](?:q|query)=([^&]+)", url_larga)
        
        if match_place: lugar_texto = unquote_plus(match_place.group(1)).replace("+", " ")
        elif match_search: lugar_texto = unquote_plus(match_search.group(1)).replace("+", " ")
        elif match_q: lugar_texto = unquote_plus(match_q.group(1)).replace("+", " ")
                
        if lugar_texto and len(lugar_texto) < 60 and not lugar_texto.startswith("EhAm"):
            logger.info(f"📍 Se encontró lugar '{lugar_texto}' en el link. Llamando API de Google Maps...")
            gps = obtener_gps_por_direccion(lugar_texto)
            if gps:
                return {"latitud": gps["latitud"], "longitud": gps["longitud"], "direccion_daxia": lugar_texto, "direccion_formateada": gps.get("direccion_formateada")}
            return {"direccion_daxia": lugar_texto}

        patron_meta = r"(?:center|markers|ll)=(-?\d{1,2}\.\d{3,})(?:%2C|,)(-?\d{1,3}\.\d{3,})"
        match_meta = re.search(patron_meta, html_content)
        if match_meta:
            return {"latitud": float(match_meta.group(1)), "longitud": float(match_meta.group(2))}
            
        patron_app = r"\[\[null,null,(-?\d{1,2}\.\d{3,}),(-?\d{1,3}\.\d{3,})\]"
        match_app = re.search(patron_app, html_content)
        if match_app:
            return {"latitud": float(match_app.group(1)), "longitud": float(match_app.group(2))}

    return None

# ============================================================
# 🔥 FUNCIÓN PARA REVIVIR MEMORIA SI LA SESIÓN EXPIRÓ
# ============================================================
def revivir_memoria_desde_historial(mensajes: list, datos: dict) -> dict:
    busqueda = datos.get("busqueda", {})
    textos_pasados = [m.content.lower() for m in mensajes if getattr(m, 'type', '') == 'ia' or getattr(m, 'role', '') == 'ai']
    
    if not busqueda.get("proposito"):
        if any("arriendo" in t or "arrendar" in t for t in textos_pasados): busqueda["proposito"] = "Arriendo"
        elif any("venta" in t or "comprar" in t for t in textos_pasados): busqueda["proposito"] = "Venta"

    for texto in reversed(textos_pasados):
        if not busqueda.get("presupuesto") and "presupuesto máximo: $" in texto:
            try: busqueda["presupuesto"] = texto.split("presupuesto máximo: $")[1].split()[0].replace(".", "")
            except: pass
        if not busqueda.get("departamento_ciudad") and "ubicación:" in texto:
            try: busqueda["departamento_ciudad"] = texto.split("ubicación:")[1].split(" ")[1]
            except: pass

    if busqueda.get("proposito"): 
        datos["zoho_deal_id"] = "REVIVIDO" 
        datos["nota_inicial_creada"] = True
    
    datos["busqueda"] = busqueda
    return datos

# ============================================================
# AGENTE ROUTER
# ============================================================
def router_agent(state: InmuebleState):
    mensajes = ensure_list_messages(state.get("messages"))
    ultimo_usuario = get_last_text(mensajes) or ""
    lower = ultimo_usuario.lower()
    lower_limpio = re.sub(r'[^a-záéíóúñ\s]', '', lower).strip()

    datos = dict(state.get("datos_inmueble", {}) or {})
    deal_id = datos.get("zoho_deal_id")
    fase_actual = datos.get("fase_recoleccion", "")
    
    # Sacamos nombre limpio para respuestas
    n_crudo = datos.get("nombre_propietario", "cliente")
    if "|" in n_crudo: n_crudo = n_crudo.split("|")[-1].strip()
    nombre_cliente = re.sub(r'\d+', '', n_crudo).strip().split(" ")[0] if n_crudo else "cliente"

    # =================================================================
    # 🎯 0. DESVÍOS PRIORITARIOS: ESTADOS DE PUENTE O AJUSTE
    # =================================================================
    
    # 🔥 A) Si está en medio de ajustes, redirigimos al recolector de búsqueda
    if fase_actual in ["mas_ajustes", "confirmar_inicio"]:
        logger.info(f"🛡️ ROUTER: Cliente en fase de ajuste ({fase_actual}). Redirigiendo a recolector_busqueda.")
        return {
            **state,
            "datos_inmueble": datos,
            "next_agent": "recolector_busqueda"
        }

    # B) Si está en puente de ejecución o espera, va directo al Ejecutor
    bridge_flags = [
        "esperando_confirmacion_codeudor", 
        "esperando_confirmacion_pago", 
        "esperando_entrega_tarjetas", 
        "esperando_recalculo"
    ]
    if any(datos.get(flag) for flag in bridge_flags):
        logger.info("🛡️ ROUTER: Cliente en puente activo. Redirigiendo a ejecutor_busqueda.")
        return {
            **state,
            "datos_inmueble": datos,
            "next_agent": "ejecutor_busqueda"
        }

    # 🔥 C) INTERCEPTOR DE RECHAZO / DESPEDIDA (CONOCEDOR DE FASE) 🔥
    if es_rechazo_definitivo(ultimo_usuario, fase_actual) and deal_id:
        logger.info("🛑 ROUTER: Rechazo definitivo detectado. Cerrando oportunidad.")
        contexto_rechazo = (
            "El cliente indicó que no desea continuar o se despidió. "
            "Despídete de forma MUY BREVE (máximo 2 oraciones), amable y profesional. "
            "Agradécele por contactar a Coninsa.\n"
            "🚨 PROHIBIDO enviar a página web o pedir que llame asesores."
        )
        msg_cierre = generar_respuesta_contextual(mensajes, ultimo_usuario, nombre_cliente, contexto_rechazo)
        
        datos["motivo_cierre"] = "Cliente desistió o canceló la búsqueda voluntariamente"
        datos["conclusion_cierre"] = "Desiste"
        datos["esperando_ajuste"] = False
        
        return {
            **state,
            "messages": mensajes + [msg_cierre],
            "datos_inmueble": datos,
            "operacion": "cerrar_oportunidad",
            "next_agent": "ejecutor_busqueda"
        }

    # 🔥 RECUPERACIÓN DE MEMORIA TRAS REINICIO 🔥
    if not deal_id and len(mensajes) > 4:
        datos = revivir_memoria_desde_historial(mensajes, datos)
        deal_id = datos.get("zoho_deal_id")

    busqueda = datos.get("busqueda", {})

    # =================================================================
    # 🎯 DETECTORES DE INTERÉS Y GPS
    # =================================================================
    
    # Interés explícito (Código o frase)
    es_interes_explicito = any(frase in lower for frase in ["me interesa", "código", "codigo", "ref", "referencia"])
    es_solo_numero_codigo = bool(re.fullmatch(r'\s*\d{4,6}\s*', lower))
    es_ubicacion = "lat=" in lower or "lng=" in lower or "ubicación:" in lower

    if (es_interes_explicito or es_solo_numero_codigo) and not es_ubicacion:
        match_codigo = re.search(r'\b(\d{4,6})\b', lower)
        if match_codigo and deal_id:
            logger.info(f"🚀 Clic de interés detectado por código {match_codigo.group(1)}.")
            return {
                **state,
                "operacion": "registrar_interes",
                "next_agent": "ejecutor_busqueda"
            }

    # Detección de Google Maps
    coordenadas = extraer_datos_de_url_maps(ultimo_usuario)
    if coordenadas:
        if "latitud" in coordenadas:
            busqueda["latitud"] = coordenadas["latitud"]
            busqueda["longitud"] = coordenadas["longitud"]
            dir_texto = coordenadas.get("direccion_daxia", "") or obtener_direccion_por_gps(coordenadas["latitud"], coordenadas["longitud"])
            busqueda["direccion_daxia"] = dir_texto
            
            # Deducción de ciudad
            ciudad_detectada = "Ciudad GPS"
            if dir_texto:
                if any(x in dir_texto.lower() for x in ["medell", "laureles", "poblado"]): ciudad_detectada = "Medellín"
                elif any(x in dir_texto.lower() for x in ["bogot", "chapinero"]): ciudad_detectada = "Bogotá"
                elif "barranquilla" in dir_texto.lower(): ciudad_detectada = "Barranquilla"
                else: ciudad_detectada = dir_texto.split(",")[-1].strip()
            
            busqueda["departamento_ciudad"] = ciudad_detectada
            busqueda["ubicacion_especifica"] = dir_texto
            
        elif "direccion_daxia" in coordenadas:
            texto_lugar = coordenadas["direccion_daxia"]
            gps = obtener_gps_por_direccion(texto_lugar)
            if gps:
                busqueda["latitud"] = gps["latitud"]
                busqueda["longitud"] = gps["longitud"]
                dir_fmt = gps.get("direccion_formateada", texto_lugar).lower()
                if "medell" in dir_fmt or "antioquia" in dir_fmt: ciudad_detectada = "Medellín"
                elif "bogot" in dir_fmt or "cundinamarca" in dir_fmt: ciudad_detectada = "Bogotá"
                elif "barranquilla" in dir_fmt or "atlantico" in dir_fmt: ciudad_detectada = "Barranquilla"
                else: ciudad_detectada = texto_lugar.split(",")[-1].strip()
            else:
                ciudad_detectada = texto_lugar.split(",")[-1].strip() if "," in texto_lugar else texto_lugar
            
            busqueda["direccion_daxia"] = texto_lugar
            busqueda["departamento_ciudad"] = ciudad_detectada
            busqueda["ubicacion_especifica"] = texto_lugar

        datos["candado_gps"] = True
        datos["busqueda"] = busqueda
        susurro = SystemMessage(content=f"INSTRUCCIÓN INTERNA: El enlace se procesó. Ubicación: '{busqueda.get('direccion_daxia')}' en '{busqueda.get('departamento_ciudad')}'. Confirma la zona y pide presupuesto.")
        
        return {
            **state, 
            "messages": mensajes + [susurro], 
            "datos_inmueble": datos, 
            "operacion": "busqueda", 
            "next_agent": "recolector_busqueda"
        }

    # =================================================================
    # 🎯 EXTRACCIÓN Y TRANSICIONES
    # =================================================================
    
    quiere_actualizar = False
    saludos_comunes = ["hola", "ola", "buenas", "buenos dias", "buenas tardes", "holis", "holi", "alo"]
    es_solo_saludo = (lower_limpio in saludos_comunes or len(lower_limpio) <= 4) and not bool(re.search(r'\d', ultimo_usuario))

    try:
        ident_check = interpretar_identidad_y_proposito(mensajes, datos.get("nombre_propietario", ""), datos.get("email_propietario", ""), datos.get("cedula_propietario", ""))
        if ident_check:
            datos["ident_check_cache"] = ident_check.dict()
            if deal_id and deal_id != "REVIVIDO":
                busqueda_check = extraer_datos_busqueda(mensajes, busqueda)
                if busqueda_check and busqueda_check.quiere_reiniciar_busqueda:
                    proposito_anterior = busqueda.get("proposito")
                    datos["busqueda"] = {"proposito": proposito_anterior} if proposito_anterior else {}
                    datos["ultimos_inmuebles"] = []
                    datos["inmuebles_precalculados"] = []
                    busqueda = datos["busqueda"]

            if ident_check.negocio_detectado: busqueda["proposito"] = ident_check.negocio_detectado
            if ident_check.tipo_inmueble_detectado: busqueda["tipo_inmueble"] = ident_check.tipo_inmueble_detectado
            
            if not datos.get("candado_gps"):
                if ident_check.ciudad_detectada: busqueda["departamento_ciudad"] = ident_check.ciudad_detectada
                if ident_check.barrio_detectado: busqueda["ubicacion_especifica"] = ident_check.barrio_detectado 

            if getattr(ident_check, "presupuesto_detectado", None): busqueda["presupuesto"] = ident_check.presupuesto_detectado
            if getattr(ident_check, "caracteristicas_detectadas", None): busqueda["caracteristicas_deseadas"] = ident_check.caracteristicas_detectadas
            if getattr(ident_check, "alcobas_detectadas", None): busqueda["numero_alcobas"] = ident_check.alcobas_detectadas

            datos["busqueda"] = busqueda
            quiere_actualizar = any([ident_check.actualizar_nombre, ident_check.actualizar_email, ident_check.actualizar_cedula, ident_check.quiere_actualizar_datos_generico, ident_check.quiere_ver_datos])
    except Exception as e:
        logger.error(f"Error check identidad router: {e}")

    # Recuperación por saludo
    if es_solo_saludo:
        if busqueda.get("proposito") or deal_id:
            susurro = SystemMessage(content=f"INSTRUCCIÓN: El usuario regresó. Buscábamos {busqueda.get('tipo_inmueble')} en {busqueda.get('proposito')} en {busqueda.get('departamento_ciudad')}. Salúdalo y pregunta si seguimos con eso.")
            return {**state, "messages": mensajes + [susurro], "datos_inmueble": datos, "operacion": "busqueda", "next_agent": "recolector_busqueda"}
        elif len(mensajes) <= 2:
            return {**state, "datos_inmueble": datos, "bienvenida_dada": True, "next_agent": "bienvenida"}

    # Política y Datos
    if not state.get("politica_aceptada"): return {**state, "datos_inmueble": datos, "next_agent": "agente_politica"}
    
    datos_completos = all([datos.get("nombre_propietario"), datos.get("email_propietario"), datos.get("cedula_propietario")])
    if not datos_completos or quiere_actualizar: return {**state, "datos_inmueble": datos, "operacion": "identidad", "next_agent": "recolector_identidad"}

    # Intenciones claras
    palabras_arriendo = ["arrendar", "arriendo", "alquilar", "renta"]
    palabras_compra = ["comprar", "compra", "venta", "vender"]
    menciona_arriendo = any(k in lower for k in palabras_arriendo) or busqueda.get("proposito") == "Arriendo"
    menciona_compra = any(k in lower for k in palabras_compra) or busqueda.get("proposito") == "Venta"
    
    if menciona_arriendo: busqueda["proposito"] = "Arriendo"
    elif menciona_compra: busqueda["proposito"] = "Venta"
    datos["busqueda"] = busqueda
        
    if busqueda.get("proposito"):
        if deal_id and deal_id != "REVIVIDO":
            return {**state, "datos_inmueble": datos, "operacion": "busqueda", "next_agent": "recolector_busqueda"}
        else:
            return {**state, "datos_inmueble": datos, "operacion": "busqueda", "modo": "busqueda_silenciosa", "next_agent": "ejecutor_identidad"}
    
    return {**state, "datos_inmueble": datos, "operacion": "busqueda", "next_agent": "recolector_busqueda"}

__all__ = ["router_agent"]