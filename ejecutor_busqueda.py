# app/agents/ejecutor_busqueda.py
import logging
import re
import json
import requests
import unicodedata
import os
import threading  # 🔥 IMPORTACIÓN PARA TAREAS EN SEGUNDO PLANO
from langchain_core.messages import AIMessage
from app.state import InmuebleState
from app.utils.messages import ensure_list_messages, get_last_text 
from app.tools.contactos import actualizar_registro_zoho_api, guardar_nota_zoho_api
from app.tools.api_coninsa_busqueda import buscar_inmuebles_coninsa 
from app.utils.evaluador_inmuebles import evaluar_descripciones_con_llm
from app.utils.charlas import generar_respuesta_contextual
from app.tools.zoho_search import buscar_producto_por_codigo_coninsa
import concurrent.futures

logger = logging.getLogger("EJECUTOR_BUSQUEDA")

def _limpiar_presupuesto_entero(texto: str) -> int:
    if not texto: return 0
    t = str(texto).lower()
    numeros = re.findall(r'\d+', t)
    if not numeros: return 0
    valor_base = float(numeros[0])
    if "millon" in t or "millón" in t or "millones" in t:
        return int(valor_base * 1000000)
    return int(re.sub(r'[^\d]', '', t))

def quitar_tildes(texto: str) -> str:
    if not texto: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn').lower()

# =================================================================
# 🔥 NUEVO: VALIDADOR DETERMINÍSTICO (LA REGLA DEL NULL VS NEGADO)
# =================================================================
def validar_requisitos_detallados(inmueble: dict, caracteristicas: str) -> bool:
    """
    Evalúa el JSON crudo del inmueble para descartar SOLO si hay una negación explícita.
    Si el dato es null o no existe, se asume que podría tenerlo y SE MANTIENE.
    """
    if not caracteristicas: 
        return True
        
    c_lower = str(caracteristicas).lower()
    
    # 1. Filtro de Mascotas
    if any(x in c_lower for x in ["mascota", "perro", "gato", "pet"]):
        # Si dice explícitamente False, se descarta. Si es null, pasa.
        if inmueble.get("acepta_mascotas") is False:
            return False
            
    # 2. Filtro de Parqueadero / Garaje
    if any(x in c_lower for x in ["parqueadero", "garaje", "carro", "vehiculo"]):
        tg = inmueble.get("total_garejes")
        # Si dice explícitamente 0 garajes, se descarta.
        if str(tg) == "0":
            return False
            
    # 3. Filtro de Servicios Comunes (Ascensor, Piscina, Gym)
    servicios = inmueble.get("servicio_comun", [])
    if isinstance(servicios, list):
        mapa_servicios = {
            "ascensor": "Ascensor",
            "piscina": "Piscina",
            "gimnasio": "Gym",
            "gym": "Gym",
            "porteria": "Porteria",
            "vigilancia": "PorterÍa 24-7",
            "bbq": "Zona bbq",
            "infantil": "Juegos infantiles",
            "juegos": "Zona de juegos"
        }
        for termino_usuario, nombre_bd in mapa_servicios.items():
            if termino_usuario in c_lower:
                for s in servicios:
                    try:
                        s_name_bd = s.get("entity", {}).get("parent", [{}])[0].get("entity", {}).get("name", "")
                        s_estado = s.get("entity", {}).get("name", "") # "S" o "N"
                        if s_name_bd.lower() == nombre_bd.lower() and str(s_estado).upper() == "N":
                            return False # Negado explícitamente ("N")
                    except Exception:
                        pass
                        
    # 4. Filtro de Distribución Interna (Balcón, Patio, Estudio)
    distribucion = inmueble.get("distribucion", [])
    if isinstance(distribucion, list):
        mapa_dist = {
            "balcon": "Balcon",
            "balcón": "Balcon",
            "patio": "Patio",
            "estudio": "Estudio"
        }
        for termino_usuario, nombre_bd in mapa_dist.items():
            if termino_usuario in c_lower:
                for d in distribucion:
                    try:
                        d_name_bd = d.get("entity", {}).get("parent", [{}])[0].get("entity", {}).get("name", "")
                        d_estado = d.get("entity", {}).get("name", "") # Cantidad "1" o "0"
                        if d_name_bd.lower() == nombre_bd.lower() and str(d_estado) == "0":
                            return False # Negado explícitamente (Cantidad "0")
                    except Exception:
                        pass
                        
    return True # Si superó todas las pruebas (o era Null), lo mantenemos para que el LLM o el cliente decidan.


def ejecutor_busqueda_agent(state: InmuebleState) -> InmuebleState:
    mensajes = ensure_list_messages(state.get("messages", []))
    ultimo_texto = get_last_text(mensajes) or ""
    
    datos = dict(state.get("datos_inmueble", {}) or {})
    busqueda = datos.get("busqueda", {})
    deal_id = datos.get("zoho_deal_id")
    operacion = state.get("operacion")
    
    # Nombre del cliente para las respuestas contextuales
    n_crudo = datos.get("nombre_propietario", "cliente")
    if "|" in n_crudo: n_crudo = n_crudo.split("|")[-1].strip()
    n_limpio = re.sub(r'\d+', '', n_crudo).strip()
    nombre_cliente = n_limpio.split(" ")[0] if n_limpio else "cliente"

    # ==================================================
    # 🔥 ARREGLO: NORMALIZACIÓN TEMPRANA DE UBICACIÓN 🔥
    # ==================================================
    ciudad_raw = busqueda.get("departamento_ciudad", "")
    ciudad_api = ciudad_raw.split('/')[0].strip() if ciudad_raw else ""

    ciudad_lower = ciudad_api.lower()
    if "bogot" in ciudad_lower: ciudad_api = "Bogotá"
    elif "medell" in ciudad_lower: ciudad_api = "Medellín"
    elif "barranquilla" in ciudad_lower: ciudad_api = "Barranquilla"

    barrio_api = busqueda.get("ubicacion_especifica", "")
    direccion_gps = busqueda.get("direccion_daxia", "")
    
    if not ciudad_api and direccion_gps:
        partes_gps = [p.strip() for p in direccion_gps.split(",")]
        if len(partes_gps) >= 2:
            barrio_api, ciudad_api = partes_gps[0], partes_gps[1]
        else:
            ciudad_api = direccion_gps

    if barrio_api and "," in barrio_api:
        barrio_api = barrio_api.split(",")[0].strip()

    if not busqueda.get("latitud") and barrio_api and ciudad_api and operacion != "cerrar_oportunidad":
        try:
            from app.agents.router import obtener_gps_por_direccion
            texto_busqueda = f"{barrio_api}, {ciudad_api}"
            logger.info(f"🗺️ Text-to-GPS: Convirtiendo '{texto_busqueda}' a coordenadas para activar búsqueda radial...")
            gps_data = obtener_gps_por_direccion(texto_busqueda)
            if gps_data:
                busqueda["latitud"] = gps_data["latitud"]
                busqueda["longitud"] = gps_data["longitud"]
        except Exception as e:
            logger.warning(f"⚠️ No se pudo convertir texto a GPS: {e}")

    # ==================================================
    # 0. EL "PUENTE INTELIGENTE" (MANEJO DE PAUSAS)
    # ==================================================
    espera_codeudor = datos.get("esperando_confirmacion_codeudor", False)
    espera_pago = datos.get("esperando_confirmacion_pago", False)
    espera_recalculo = datos.get("esperando_recalculo", False)
    espera_entrega = datos.get("esperando_entrega_tarjetas", False)
    
    busqueda_en_fondo_activa = datos.get("busqueda_en_fondo_activa", False)

    confirmaciones = ["si", "sí", "claro", "listo", "ok", "dale", "entendido", "bien", "perfecto", 
                      "muestra", "adelante", "credito", "crédito", "banco", "propios", "contado", "efectivo",
                      "envialas", "mandamelas", "quiero ver", "enseñame", "gracias", "asi esta bien", "ya"]
    
    texto_limpio_conf = re.sub(r'[^a-záéíóúñ\s]', '', ultimo_texto.lower()).strip()
    usuario_confirma = any(word in ultimo_texto.lower() for word in confirmaciones) or len(texto_limpio_conf) < 4

    inmuebles_encontrados = []
    busqueda_exacta_exitosa = False
    mensaje_tipo_resultado = ""

    if (espera_codeudor or espera_pago or espera_recalculo or espera_entrega) and not busqueda_en_fondo_activa:
        logger.info(f"🌉 Cliente en puente. Analizando respuesta: '{ultimo_texto}'")

        # 🔥 CASO A: EL PUENTE ABIERTO (Solo esperando permiso para entregar)
        if espera_entrega:
            if not usuario_confirma:
                logger.info("🔄 El cliente quiere cambiar algo antes de ver. Soltando al router.")
                datos["esperando_entrega_tarjetas"] = False
                return {**state, "datos_inmueble": datos, "next_agent": "router"}
                
            logger.info("✅ Cliente quiere ver las opciones. Entregando...")
            datos["esperando_entrega_tarjetas"] = False
            inmuebles_listos = datos.get("inmuebles_precalculados", [])
            
            if inmuebles_listos:
                inmuebles_encontrados = inmuebles_listos
                busqueda_exacta_exitosa = True 
                mensaje_tipo_resultado = "perfecto"
                datos["inmuebles_precalculados"] = []
            else:
                logger.info("⏳ Maleta vacía. Ejecutando búsqueda en vivo.")

        # 🔥 CASO B: LOS PUENTES DE BÚSQUEDA (Codeudor, Pago, Recálculo)
        else:
            if espera_codeudor:
                contexto_decision = (
                    "Confirmación de codeudor. Si acepta, avísale  que ya tienes los resultados listos. PREGÚNTALE: '¿Deseas que te las envíe?'"
                )
            elif espera_pago:
                contexto_decision = (
                    "Confirmación de pago. Si acepta, dile que esta bien y avísale que ya tienes las opciones listas. PREGÚNTALE: '¿Deseas que las miremos?'"
                )
            else:
                contexto_decision = (
                    "Recálculo. Si confirma, avísale que ya tienes las nuevas opciones listas. PREGÚNTALE: '¿Quieres que las miremos?'"
                )

            res_ali = generar_respuesta_contextual(mensajes, ultimo_texto, nombre_cliente, contexto_decision)
            
            if usuario_confirma:
                logger.info("✅ Cliente confirmó en el puente. Pasando a Modo Entrega.")
                
                if espera_codeudor: datos["esperando_confirmacion_codeudor"] = False
                if espera_pago: datos["esperando_confirmacion_pago"] = False
                if espera_recalculo: datos["esperando_recalculo"] = False
                
                if espera_pago:
                    if "credit" in ultimo_texto.lower() or "banco" in ultimo_texto.lower():
                        busqueda["metodo_pago"] = "Crédito"
                    else:
                        busqueda["metodo_pago"] = "Recursos Propios"
                    datos["busqueda"] = busqueda
                
                # PRENDEMOS EL FRENO ABIERTO para el siguiente turno
                datos["esperando_entrega_tarjetas"] = True
                
                return {
                    **state, 
                    "messages": mensajes + [AIMessage(content=res_ali)], 
                    "datos_inmueble": datos,
                    "operacion": "busqueda_validar", # 🔥 EL GATILLO QUE DESPIERTA A TU ORQUESTADOR
                    "next_agent": "__end__" 
                }
            else:
                logger.info("💬 Cliente tiene dudas en el puente. Respondiendo y pausando flujo.")
                return {
                    **state, 
                    "messages": mensajes + [AIMessage(content=res_ali)], 
                    "datos_inmueble": datos,
                    "next_agent": "__end__" 
                }

    # ==================================================
    # 🎯 FASE 2: PROCESAR EL CLIC EN "ME INTERESA" 
    # ==================================================
    if operacion == "registrar_interes":
        match = re.search(r"\b(\d{4,8})\b", ultimo_texto)
        codigo = match.group(1) if match else None
        
        if deal_id and codigo:
            inmuebles_previos = datos.get("ultimos_inmuebles", [])
            inmueble_sel = next((i for i in inmuebles_previos if str(i.get('id', i.get('codigo_abr_inmueble'))) == codigo), None)
            
            if not inmueble_sel:
                logger.info(f"🔎 Inmueble {codigo} no está en memoria. Consultando API de Coninsa directamente...")
                inmueble_sel = {"id": codigo} 
                try:
                    prop_busqueda = busqueda.get("proposito", "arriendo").lower()
                    endpoint = "inmuebles-venta" if "venta" in prop_busqueda else "inmuebles-arriendo"
                    url_api = f"https://api.coninsa.co/api/v2/{endpoint}/{codigo}"
                    
                    res_api = requests.get(url_api, timeout=8)
                    if res_api.status_code == 200:
                        data_json = res_api.json()
                        if isinstance(data_json, list) and len(data_json) > 0:
                            inmueble_sel = data_json[0]
                        elif isinstance(data_json, dict) and "id" in data_json:
                            inmueble_sel = data_json
                        logger.info(f"✅ ¡Datos del inmueble {codigo} descargados con éxito!")
                except Exception as e:
                    logger.error(f"❌ Falló la consulta directa a la API para el inmueble {codigo}: {e}")

            exito = registrar_interes_inmueble(deal_id, inmueble_sel, busqueda.get("proposito", "Arriendo"), busqueda.get("departamento_ciudad", "Ciudad"))
            
            if exito:
                confirmacion = " Ya registré tu interés por este inmueble en nuestro sistema. Un asesor te contactará muy pronto para darte más detalles."
                
                # 🔥 EL ÚNICO LUGAR DONDE SE BORRA LA MEMORIA ES EN LA FASE 2
                datos["zoho_deal_id"] = None
                datos["busqueda"] = {}
                datos["proposito"] = None
                datos["ultimos_inmuebles"] = []
                datos["nota_inicial_creada"] = False
                
                return {
                    **state,
                    "messages": mensajes + [AIMessage(content=confirmacion)],
                    "datos_inmueble": datos,
                    "operacion": "esperando_interes",
                    "next_agent": "__end__"
                }

    # ==================================================
    # 🔍 FASE 1: EJECUCIÓN DE LA BÚSQUEDA (API + LLM)
    # ==================================================
    if not inmuebles_encontrados and operacion != "cerrar_oportunidad":
        logger.info("🔍 [FASE 1] Iniciando búsqueda en API Coninsa...")
        
        proposito_raw = busqueda.get("proposito", "").lower()
        servicio_api = "AR" if "arriendo" in proposito_raw else "CO"
        presupuesto_max = _limpiar_presupuesto_entero(busqueda.get("presupuesto", ""))
        
        tipo_inm = busqueda.get("tipo_inmueble")
        tipo_inm_fmt = tipo_inm.capitalize() if tipo_inm else "Apartamento"

        alcobas_pedidas = str(busqueda.get("numero_alcobas", ""))
        banos_pedidos = str(busqueda.get("numero_banos", ""))
        caracteristicas_deseadas = busqueda.get("caracteristicas_deseadas", "")

        # Respetando tu payload con los campos exactos
        payload_api = {
            "Ciudad": "" if busqueda.get("latitud") else ciudad_api,
            "Barrio": "" if busqueda.get("latitud") else barrio_api,
            "TipoInmueble": tipo_inm_fmt,
            "ValorHasta": str(presupuesto_max) if presupuesto_max > 0 else "",
            "Banos": banos_pedidos,
            "Habitacion": alcobas_pedidas,
            "Area": str(busqueda.get("area_minima", "")),
            "Servicio": servicio_api,
            "latitud": busqueda.get("latitud"),
            "longitud": busqueda.get("longitud")
        }
        
        inmuebles_brutos = buscar_inmuebles_coninsa(payload_api)
        
        if inmuebles_brutos:
            logger.info(f"✅ La API devolvió {len(inmuebles_brutos)} opciones iniciales.")
            inmuebles_exactos = []
            inmuebles_amplios = []
            
            # 🔥 CASCADA 1: FILTRO MATEMÁTICO Y DE "NEGADOS" 🔥
            for inm in inmuebles_brutos:
                alcobas_inm = str(inm.get("alcobas", ""))
                banos_inm = str(inm.get("banos", ""))
                
                cumple_alcobas = (alcobas_pedidas == "" or alcobas_inm == alcobas_pedidas)
                cumple_banos = (banos_pedidos == "" or banos_inm == banos_pedidos)
                
                # LA NUEVA MAGIA: Si el inmueble dice explícitamente "NO", se descarta aquí mismo
                pasa_filtro_negacion = validar_requisitos_detallados(inm, caracteristicas_deseadas)
                
                if pasa_filtro_negacion:
                    inm["_es_exacto"] = cumple_alcobas and cumple_banos
                    if inm["_es_exacto"]:
                        inmuebles_exactos.append(inm)
                    else:
                        inmuebles_amplios.append(inm)
                else:
                    # El inmueble queda descartado en silencio por no cumplir un requisito obligatorio
                    pass

            ids_que_cumplen_desc = []
            ids_rechazados = []
            
            # 🔥 CASCADA 2: FILTRO LLM MASIVO (Max 80 Inmuebles) 🔥
            # Ahora el LLM recibe una lista muchísimo más limpia porque ya sacamos la basura "negada"
            candidatos_para_llm = inmuebles_exactos + inmuebles_amplios
            debe_evaluar_llm = caracteristicas_deseadas and len(candidatos_para_llm) > 0

            if debe_evaluar_llm:
                if len(candidatos_para_llm) > 80:
                    logger.warning(f"⚠️ Demasiados inmuebles ({len(candidatos_para_llm)}). Recortando a 80 para el LLM.")
                    candidatos_para_llm = candidatos_para_llm[:80]

                logger.info(f"🧠 Consultando LLM en PARALELO para {len(candidatos_para_llm)} opciones con: {caracteristicas_deseadas}")
                
                tamano_bloque = 5
                bloques = [candidatos_para_llm[i:i + tamano_bloque] for i in range(0, len(candidatos_para_llm), tamano_bloque)]
                
                def evaluar_bloque(bloque_inmuebles):
                    try:
                        return evaluar_descripciones_con_llm(bloque_inmuebles, caracteristicas_deseadas)
                    except Exception as e:
                        logger.error(f"❌ Error en hilo de LLM: {e}")
                        return {"ids_cumplen": [], "ids_rechazados": []}
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(bloques), 15)) as executor:
                    resultados_hilos = list(executor.map(evaluar_bloque, bloques))
                    
                for res in resultados_hilos:
                    ids_que_cumplen_desc.extend(res.get("ids_cumplen", []))
                    ids_rechazados.extend(res.get("ids_rechazados", []))
                
                logger.info(f"✅ Lectura en paralelo terminada. Analizados: {len(candidatos_para_llm)}")
            else:
                if caracteristicas_deseadas:
                    logger.info("⚡ Saltando evaluación de LLM.")
                ids_que_cumplen_desc = [str(i.get("id", i.get("codigo_abr_inmueble", ""))) for i in candidatos_para_llm]

            def _cumple_desc(inm):
                if not caracteristicas_deseadas: return True 
                inm_id = str(inm.get("id", inm.get("codigo_abr_inmueble", "")))
                return inm_id in ids_que_cumplen_desc
            
            # LA PURGA FINAL
            inmuebles_exactos = [inm for inm in inmuebles_exactos if str(inm.get("id", inm.get("codigo_abr_inmueble", ""))) not in ids_rechazados]
            inmuebles_amplios = [inm for inm in inmuebles_amplios if str(inm.get("id", inm.get("codigo_abr_inmueble", ""))) not in ids_rechazados]
            inmuebles_purga = [inm for inm in inmuebles_brutos if str(inm.get("id", inm.get("codigo_abr_inmueble", ""))) not in ids_rechazados and validar_requisitos_detallados(inm, caracteristicas_deseadas)]

            # 🔥 EL TORNEO: (Max 10 para Twilio) 🔥
            candidatos_nivel_1 = [inm for inm in inmuebles_exactos if _cumple_desc(inm)]
            candidatos_nivel_2 = inmuebles_exactos
            candidatos_nivel_3 = [inm for inm in inmuebles_amplios if _cumple_desc(inm)]
            
            if len(candidatos_nivel_1) > 0:
                inmuebles_encontrados = candidatos_nivel_1[:10]
                busqueda_exacta_exitosa = True
                mensaje_tipo_resultado = "perfecto"
            elif len(candidatos_nivel_2) > 0:
                inmuebles_encontrados = candidatos_nivel_2[:10]
                busqueda_exacta_exitosa = True
                mensaje_tipo_resultado = "exacto_numeros"
            elif len(candidatos_nivel_3) > 0:
                inmuebles_encontrados = candidatos_nivel_3[:10]
                busqueda_exacta_exitosa = False
                mensaje_tipo_resultado = "amplio_descripcion"
            elif len(inmuebles_purga) > 0:
                inmuebles_encontrados = inmuebles_purga[:10]
                busqueda_exacta_exitosa = False
                mensaje_tipo_resultado = "respaldo"

        datos["inmuebles_encontrados"] = inmuebles_encontrados
        datos["ultimos_inmuebles"] = inmuebles_encontrados 

    # ==================================================
    # 📝 ACTUALIZACIÓN EN ZOHO CRM (FASE 1) - BACKGROUND THREAD
    # ==================================================
    if deal_id and operacion != "registrar_interes" and not busqueda_en_fondo_activa:
        logger.info(f"🚀 EJECUTOR: Procesando Deal {deal_id} - ENVÍO FASE 1")
        
        datos_crm = {}
        if busqueda.get("proposito"): datos_crm["Tipo_de_Oportunidad"] = busqueda.get("proposito").capitalize()
        if busqueda.get("proposito"): datos_crm["Tipo_de_servicio"] = busqueda.get("proposito").capitalize()
        if busqueda.get("uso_propiedad"): datos_crm["Uso_de_la_propiedad"] = busqueda.get("uso_propiedad")
        if busqueda.get("tipo_inmueble"): datos_crm["Tipo_de_Propiedad"] = busqueda.get("tipo_inmueble").capitalize()

        if busqueda.get("presupuesto"):
            monto = _limpiar_presupuesto_entero(busqueda.get("presupuesto"))
            if monto > 0: datos_crm["Valor_canon_precio"] = str(monto)

        if busqueda.get("numero_alcobas"):
            try: datos_crm["No_Alcobas"] = str(int(busqueda.get("numero_alcobas"))) 
            except (ValueError, TypeError): pass

        if busqueda.get("metodo_pago"):
            datos_crm["Medio_de_pago"] = busqueda.get("metodo_pago")

        ciudad_zoho = ciudad_api if ciudad_api else "Ciudad sin definir"
        ciudad_lower = ciudad_zoho.lower()
        if "medell" in ciudad_lower: ciudad_zoho = "Medellín" 
        elif "bogot" in ciudad_lower: ciudad_zoho = "Bogota D.C."

        barrio_zoho = barrio_api if barrio_api else "Zona General"
        datos_crm["Direcci_n_del_inmueble"] = f"{barrio_zoho}, {ciudad_zoho}"

        tiempo_mapeado = busqueda.get("tiempo_necesidad", "Por definir")
        if tiempo_mapeado and tiempo_mapeado != "Por definir":
            datos_crm["Para_cu_ndo_necesita_el_inmueble"] = tiempo_mapeado

        if operacion == "cerrar_oportunidad":
            motivo_detalle = datos.get("conclusion_cierre", datos.get("motivo_cierre", "No cumple filtros"))
            datos_crm["Estado_de_oportunidad"] = "Perdida" 
            datos_crm["Conclusi_n_de_Oportunidad"] = "Se descarta" 
            datos_crm["Raz_n_de_descarte_Oportunidad"] = motivo_detalle
        else:
            datos_crm["Estado_de_oportunidad"] = "Activa"

        resumen_lineas = []
        if busqueda.get('latitud') and busqueda.get('longitud'):
            lugar_gps = busqueda.get('direccion_daxia', f"{barrio_zoho}, {ciudad_zoho}")
            resumen_lineas.append(f"📍 Búsqueda por GPS: {lugar_gps}")
        if busqueda.get('proposito'): resumen_lineas.append(f"• Propósito: {busqueda.get('proposito')}")
        if busqueda.get('metodo_pago'): resumen_lineas.append(f"• Método de Pago: {busqueda.get('metodo_pago')}")
        if busqueda.get('tipo_inmueble'):
            uso = busqueda.get('uso_propiedad', 'Vivienda')
            resumen_lineas.append(f"• Tipo y Uso: {busqueda.get('tipo_inmueble')} para {uso}")
        if ciudad_zoho != "Ciudad sin definir" or barrio_zoho != "Zona General":
            resumen_lineas.append(f"• Ubicación: {ciudad_zoho} {barrio_zoho}".strip())
        if busqueda.get('numero_alcobas'): resumen_lineas.append(f"• Alcobas: {busqueda.get('numero_alcobas')}")
        if busqueda.get('numero_banos'): resumen_lineas.append(f"• Baños: {busqueda.get('numero_banos')}")
        if busqueda.get('presupuesto'): resumen_lineas.append(f"• Presupuesto Máximo: ${busqueda.get('presupuesto')}")
        if tiempo_mapeado != "Por definir": resumen_lineas.append(f"• Tiempo Estimado: {busqueda.get('tiempo_literal', 'Por definir')} | (CRM: {tiempo_mapeado})")
        
        caracteristicas = busqueda.get('caracteristicas_deseadas', '')
        if caracteristicas: resumen_lineas.append(f"\n🏡 CARACTERÍSTICAS ESPECIALES: {caracteristicas}")
        
        resumen_base = "\n".join(resumen_lineas)

        es_actualizacion = datos.get("nota_inicial_creada", False)
        if not es_actualizacion: datos["nota_inicial_creada"] = True
        motivo_cierre = datos.get("conclusion_cierre", "")

        def actualizar_zoho_en_segundo_plano(d_id, d_crm, is_upd, r_base, hist_mensajes, op, c_motivo):
            try:
                actualizar_registro_zoho_api("Deals", d_id, d_crm)
                if op == "cerrar_oportunidad":
                    t_nota = "Descarte (Ali)"
                    c_nota = f"❌ OPORTUNIDAD DESCARTADA\nMotivo: {c_motivo}"
                else:
                    if is_upd:
                        ctx_resumen = "Haz un resumen de una sola oración indicando EXCLUSIVAMENTE los ajustes de características, presupuesto o ubicación que el cliente acaba de pedir. Ignora confirmaciones como 'sí' o 'gracias'."
                        try: resumen_cambios = generar_respuesta_contextual(hist_mensajes, "Resume los cambios recientes", "Cliente", ctx_resumen)
                        except: resumen_cambios = "El cliente realizó ajustes en sus preferencias."
                        t_nota = "🔄 Actualización Fase 1"
                        c_nota = f"🔄 ACTUALIZACIÓN DE BÚSQUEDA:\n\n{resumen_cambios}\n\n--------------------------------------------------\nNUEVO PERFIL:\n{r_base}"
                    else:
                        t_nota = "Resumen Inicial - Fase 1"
                        c_nota = f"RESUMEN DE LA PRIMERA BÚSQUEDA:\n\n{r_base}"
                guardar_nota_zoho_api(d_id, t_nota, c_nota)
                logger.info(f"✅ Nota en Zoho actualizada para Deal {d_id}.")
            except Exception as e:
                logger.error(f"❌ Error en hilo de Zoho: {e}")

        threading.Thread(target=actualizar_zoho_en_segundo_plano, args=(deal_id, datos_crm, es_actualizacion, resumen_base, mensajes[:-1], operacion, motivo_cierre)).start()

    # ==================================================
    # 🗣️ RESPUESTA PARA WHATSAPP (MENSAJE + TARJETAS + LISTA)
    # ==================================================
    
    # Si estaba en fondo, solo guarda y termina el ciclo silenciosamente
    if busqueda_en_fondo_activa:
        logger.info("✅ Búsqueda de fondo terminada. Guardando resultados silenciosamente.")
        datos["busqueda_en_fondo_activa"] = False
        datos["inmuebles_precalculados"] = inmuebles_encontrados
        return {**state, "datos_inmueble": datos, "next_agent": "__end__"}

    plantilla_inmuebles = None 
    texto_respuesta = ""

    if operacion != "cerrar_oportunidad":
        mensaje_recolector = ""
        if mensajes and isinstance(mensajes[-1], AIMessage):
            ultimo_msg = mensajes.pop()
            mensaje_recolector = ultimo_msg.content + "\n\n"

        ultimo_req = get_last_text(mensajes) or "Genera la respuesta con las opciones encontradas."

        if inmuebles_encontrados:
            contexto_ejecutor = (
                f"Acabas de buscar y vas a entregar {len(inmuebles_encontrados)} opciones. "
                f"Si el nivel de coincidencia es 'amplio_descripcion' o 'respaldo', aclara brevemente que ampliaste un poco la zona. "
                f"NO LISTES INMUEBLES NI DIGAS CÓDIGOS. Solo haz una introducción corta y feliz de 1 o 2 oraciones."
            )
            texto_llm = generar_respuesta_contextual(mensajes, ultimo_req, nombre_cliente, contexto_ejecutor)
            
            es_arriendo = "arriendo" in busqueda.get("proposito", "").lower()
            proposito_url = "arriendo" if es_arriendo else "venta"
            lista_plantillas_enviar = []
            
            # 🔥 NUEVO FORMATO DE LISTA CON ENLACES WEB 🔥
            textos_inmuebles = [f"🏡 *Tengo estas {len(inmuebles_encontrados)} opciones para ti:*\n"]

            for idx, inm in enumerate(inmuebles_encontrados):
                ubicacion = "Sin especificar"
                if "ubicacion" in inm and "entity" in inm["ubicacion"]:
                    ubicacion = str(inm["ubicacion"]["entity"].get("barrio", "Sin especificar")).title()
                elif "barrio" in inm and isinstance(inm["barrio"], dict):
                    ubicacion = str(inm["barrio"].get("entity", {}).get("name", "Sin especificar")).title()
                
                alcobas = str(inm.get("alcobas") or inm.get("Alcobas", "-"))
                banos = str(inm.get("banos") or inm.get("Banos", "-"))
                area_raw = str(inm.get("area") or inm.get("fieldArea", "-"))
                area = area_raw.split(".")[0] if "." in area_raw else area_raw
                codigo = str(inm.get("codigo_abr_inmueble", inm.get("id", "")))
                tipo_inm_nombre = str(inm.get("tipo_inmueble", {}).get("entity", {}).get("name", "apartamento")).title()
                
                if es_arriendo: 
                    precio = inm.get("valor_arr") or inm.get("Valor_del_inmueble_arriendo") or inm.get("fieldLeaseValue")
                else: 
                    precio = inm.get("valor_venta") or inm.get("Valor_del_inmueble_venta")
                
                precio_fmt = f"${precio:,.0f}".replace(",", ".") if isinstance(precio, (int, float)) else f"${precio}"

                imagen_url = "https://www.coninsa.co/themes/custom/coninsa/logo.svg" 
                for key in ["imagenes_nuwwe", "imagenes_sinco", "fieldImages", "imagenes"]:
                    val = inm.get(key)
                    if isinstance(val, str):
                        try: val = json.loads(val)
                        except: pass
                    if isinstance(val, list) and len(val) > 0:
                        primer_img = val[0]
                        if isinstance(primer_img, dict):
                            img_link = primer_img.get("uri") or primer_img.get("url") or primer_img.get("src")
                            if img_link:
                                imagen_url = img_link
                                break
                        elif isinstance(primer_img, str) and primer_img.startswith("http"):
                            imagen_url = primer_img
                            break
                
                if imagen_url == "https://www.coninsa.co/themes/custom/coninsa/logo.svg" and inm.get("imagen_principal"):
                    imagen_url = str(inm.get("imagen_principal"))

                partes = []
                if alcobas not in ["-", "0", "None"]: partes.append(f"{alcobas} alcobas")
                if banos not in ["-", "0", "None"]: partes.append(f"{banos} baños")
                if area not in ["-", "0", "None"]: partes.append(f"{area} m2")
                caract_resumen = ", ".join(partes) if partes else "Consulta disponibilidad"
                caract_resumen += f" | {precio_fmt}"

                # Creación de URL para WhatsApp
                tipo_url = f"{tipo_inm_nombre.lower()}-en-{proposito_url}".replace(" ", "-")
                barrio_url = re.sub(r'[^a-z0-9]', '-', quitar_tildes(ubicacion))
                ciudad_url = re.sub(r'[^a-z0-9]', '-', quitar_tildes(ciudad_api))
                url_web = f"https://www.coninsa.co/inmuebles/{tipo_url}/{barrio_url}/{ciudad_url}/{codigo}"

                textos_inmuebles.append(f"🔹 *Opción {idx+1}: {tipo_inm_nombre} en {ubicacion}* (Cód: {codigo})")
                textos_inmuebles.append(f"   🛏️ {caract_resumen}")
                textos_inmuebles.append(f"   🔗 Más info: {url_web}\n")

                # Tarjetas Twilio
                plantilla_individual = {
                    "type": "template",
                    "template_id": "HX15cc6d1520ed062f63cd165286332cb2", 
                    "variables": {
                        "1": imagen_url,
                        "2": ubicacion,
                        "3": caract_resumen
                    }
                }
                lista_plantillas_enviar.append(plantilla_individual)
            
            plantilla_inmuebles = {"type": "multiple_templates", "templates": lista_plantillas_enviar}
            texto_cierre = "¿Cuál te gusta más? Dime el número de la opción o el código. Si prefieres, también podemos ajustar alguna característica."
            
            texto_respuesta = mensaje_recolector + texto_llm + "\n\n" + "\n".join(textos_inmuebles) + "\n" + texto_cierre
            
        else:
            # 🔥 SALVAVIDAS ANTI-TIMEOUT: Si no hay opciones, avisa y no se queda callado 🔥
            logger.info("❌ 0 resultados en la búsqueda. Enviando mensaje de fallo elegante.")
            contexto_vacio = (
                f"Al ir a la base de datos, NO encontraste inmuebles disponibles con los requisitos exactos del cliente. "
                f"Redacta un mensaje muy empático informando esto. "
                f"Invítalo OBLIGATORIAMENTE a continuar la búsqueda preguntando de forma natural: "
                f"'¿Te parece si ampliamos un poco el área de búsqueda, ajustamos el presupuesto o quitamos alguna característica?'"
            )
            texto_llm = generar_respuesta_contextual(mensajes, ultimo_req, nombre_cliente, contexto_vacio)
            texto_respuesta = mensaje_recolector + texto_llm

        mensajes.append(AIMessage(content=texto_respuesta))

    return {
        **state, 
        "messages": mensajes, 
        "datos_inmueble": datos, 
        "operacion": "esperando_interes", 
        "plantilla_twilio": plantilla_inmuebles, 
        "next_agent": "__end__"
    }

# ==================================================
# FASE 2: FUNCIÓN PARA "ME INTERESA" 
# ==================================================
def registrar_interes_inmueble(deal_id: str, inmueble: dict, proposito_busqueda: str, ciudad_busqueda: str):
    import os
    codigo = str(inmueble.get("codigo_abr_inmueble", inmueble.get("id", "")))
    
    api_domain = os.environ.get("ZOHO_API_BASE", "https://www.zohoapis.com")
    zoho_product_id = buscar_producto_por_codigo_coninsa(api_domain, codigo)

    tipo_real = "Apartamento" 
    if "tipo_inmueble" in inmueble and "entity" in inmueble["tipo_inmueble"]:
        tipo_real = str(inmueble["tipo_inmueble"]["entity"].get("name", "Apartamento")).capitalize()
        
    uso_real = "Vivienda"
    if "uso_inmueble" in inmueble and "entity" in inmueble["uso_inmueble"]:
        uso_real = str(inmueble["uso_inmueble"]["entity"].get("name", "Vivienda")).capitalize()

    es_arriendo = "arriendo" in str(proposito_busqueda).lower()
    precio_crudo = str(inmueble.get("valor_arr") if es_arriendo else inmueble.get("valor_venta"))
    precio_match = re.search(r'(\d+)', precio_crudo.replace('.', '').replace(',', ''))
    precio_str = int(precio_match.group(1)) if precio_match else None

    alcobas_raw = str(inmueble.get("alcobas", ""))
    alcobas_match = re.search(r'(\d+)', alcobas_raw)
    alcobas_real = int(alcobas_match.group(1)) if alcobas_match else None

    area_raw = str(inmueble.get("area", ""))
    area_match = re.search(r'(\d+)', area_raw.replace(',', '.'))
    area_real = int(area_match.group(1)) if area_match else None

    banos_real = str(inmueble.get("banos", ""))
    parqueadero_real = str(inmueble.get("parqueadero") or inmueble.get("parqueadero_nomenclatura") or "").strip()
    cuarto_util_real = str(inmueble.get("cuarto_util") or inmueble.get("cuarto_util_nomenclatura") or "").strip()

    barrio_real = ""
    ciudad_real = ""

    ubi = inmueble.get("ubicacion", {})
    if isinstance(ubi, dict) and "entity" in ubi:
        barrio_real = str(ubi["entity"].get("barrio", "")).strip()
        padres = ubi["entity"].get("parent", [])
        if padres and isinstance(padres, list):
            ciudad_real = str(padres[0].get("entity", {}).get("ciudad", "")).strip()

    if not ciudad_real or ciudad_real.lower() == "none":
        ciudad_limpia = str(ciudad_busqueda).split('/')[0].strip().title()
        if "medell" in ciudad_limpia.lower(): ciudad_real = "Medellín"
        elif "bogot" in ciudad_limpia.lower(): ciudad_real = "Bogotá"
        else: ciudad_real = ciudad_limpia

    if not barrio_real or barrio_real.lower() == "none":
        barrio_real = "Zona General"

    ubicacion_texto = f"{barrio_real.title()}, {ciudad_real.title()}"
    direccion_db = str(inmueble.get("direccion", "")).strip()
    
    if direccion_db:
        direccion_real = f"{direccion_db} ({ubicacion_texto})"
    else:
        direccion_real = ubicacion_texto

    datos_cierre = {
        "C_digo_del_Inmueble": codigo,
        "Direcci_n_del_inmueble": direccion_real,    
        "Tipo_de_Propiedad": tipo_real,
        "Uso_de_la_propiedad": uso_real,
        "Estado_de_oportunidad": "Activa"
    }

    if zoho_product_id:
        logger.info(f"🔗 Vinculando Deal {deal_id} con Producto Zoho ID {zoho_product_id}")
        datos_cierre["Inmueble_asociado"] = {"id": zoho_product_id}
    
    if precio_str: datos_cierre["Valor_canon_precio"] = precio_str
    if alcobas_real: datos_cierre["No_Alcobas"] = alcobas_real
    if area_real: datos_cierre["rea_de_la_propiedad_m2"] = area_real 
    
    if parqueadero_real and parqueadero_real.lower() not in ["none", "null", "no", "-"]: 
        datos_cierre["Parqueadero_nomenclatura"] = parqueadero_real
    if cuarto_util_real and cuarto_util_real.lower() not in ["none", "null", "no", "-"]: 
        datos_cierre["Cuarto_til_nomenclatura"] = cuarto_util_real 
         
    texto_nota2 = f"📌 INMUEBLE SELECCIONADO (FASE 2)\n\n"
    texto_nota2 += f"El cliente ha seleccionado el Inmueble con Código: {codigo}\n\n"
    texto_nota2 += f"📋 DATOS REALES DEL INMUEBLE:\n"
    texto_nota2 += f"• Tipo y Uso: {tipo_real} para {uso_real}\n"
    texto_nota2 += f"• Dirección Completa: {direccion_real}\n"
    if precio_str: texto_nota2 += f"• Valor Real: ${int(precio_str):,.0f}\n".replace(",", ".")
    if alcobas_real: texto_nota2 += f"• Habitaciones: {alcobas_real}\n"
    if banos_real: texto_nota2 += f"• Baños: {banos_real}\n"
    if area_real: texto_nota2 += f"• Área: {area_real} m2\n"
    if parqueadero_real: texto_nota2 += f"• Parqueadero: {parqueadero_real}\n"
    if cuarto_util_real: texto_nota2 += f"• Cuarto útil: {cuarto_util_real}\n"

    try:
        actualizar_registro_zoho_api("Deals", deal_id, datos_cierre)
        guardar_nota_zoho_api(deal_id, "Inmueble Seleccionado (Fase 2)", texto_nota2)
        return True
    except Exception as e:
        print(f"❌ Error crítico al registrar el interés en Zoho: {e}")
        return False

__all__ = ["ejecutor_busqueda_agent", "registrar_interes_inmueble"]