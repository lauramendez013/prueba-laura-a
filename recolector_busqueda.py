# app/agents/recolector_busqueda.py
import logging
import re
import unicodedata

from app.state import InmuebleState
from app.utils.messages import ensure_list_messages, get_last_text
from app.utils.intent import extraer_datos_busqueda
from app.utils.charlas import generar_respuesta_contextual
from app.tools.reglas_db import obtener_ticket_minimo_sync, obtener_ciudades_cobertura_sync 

logger = logging.getLogger("RECOLECTOR_BUSQUEDA")

# =================================================================
# HELPERS DE LIMPIEZA
# =================================================================

def quitar_tildes(texto: str) -> str:
    if not texto:
        return ""
    texto_limpio = ''.join(
        c for c in unicodedata.normalize('NFD', str(texto))
        if unicodedata.category(c) != 'Mn'
    )
    return texto_limpio.lower()

def _limpiar_numero(texto: str) -> int:
    """Extrae SOLO el número de un texto y traduce palabras a dígitos."""
    if not texto: return 0
    if isinstance(texto, int): return texto
    
    t = quitar_tildes(str(texto)).strip()
    
    # 🔥 TRADUCTOR DE PALABRAS A NÚMEROS 🔥
    dict_numeros = {
        "un": 1, "uno": 1, "una": 1, 
        "dos": 2, "tres": 3, "cuatro": 4, 
        "cinco": 5, "seis": 6, "siete": 7, 
        "ocho": 8, "nueve": 9, "diez": 10
    }
    
    for palabra, numero in dict_numeros.items():
        if re.search(rf'\b{palabra}\b', t):
            return numero

    # Buscamos solo los dígitos si no encontró palabras
    numeros = re.findall(r'\d+', t)
    if not numeros: return 0
    
    valor_base = float(numeros[0])
    # Si el usuario escribió "millones", multiplicamos
    if "millon" in t:
        return int(valor_base * 1000000)
    return int(valor_base)

def es_rechazo_definitivo(texto: str, fase: str = "") -> bool:
    """
    Detecta si el usuario quiere CERRAR la oportunidad.
    Si el mensaje tiene palabras de ajuste (habitaciones, sol, etc), NO es rechazo.
    """
    t = re.sub(r'[^a-záéíóúñ\s]', '', str(texto).lower()).strip()
    t = re.sub(r'\s+', ' ', t) 
    
    # ❌ NO ES RECHAZO si está ajustando la búsqueda
    terminos_ajuste = [
        "habitacion", "alcoba", "baño", "bano", "mascota", "piso", "sol", 
        "ascensor", "parqueadero", "balcon", "necesito", "mejor", 
        "quitalo", "cambia", "otra cosa", "sin ", "no quiero que tenga", 
        "estudio", "cocina", "vista"
    ]
    if any(x in t for x in terminos_ajuste):
        return False

    # ✅ SÍ ES RECHAZO si es despedida o desinterés total
    despedidas = [
        "adios", "chao", "hasta luego", "salir", "ya no quiero mas", 
        "no me interesa continuar", "no me gusto ninguno", "ninguno me gusto", 
        "miremos en otra parte", "no mas", "nada mas", "cancelar", "finalizar",
        "no me interesa ninguno", "ya no quiero"
    ]
    if any(x in t for x in despedidas):
        return True

    # "No" seco sin nada más cierra, SALVO que estemos en la fase de preguntar si quiere más ajustes
    if t in ["no", "nop", "ninguno", "no gracias", "asi esta bien", "deja asi"]:
        if fase == "mas_ajustes":
            return False # Un "no" aquí significa "no quiero más ajustes, avancemos"
        return True
        
    return False

def es_afirmacion_busqueda(texto: str) -> bool:
    """Lista exhaustiva de sinónimos para dar permiso de avanzar."""
    t = quitar_tildes(texto).strip()
    
    confirmaciones = [
        "si", "sí", "dale", "listo", "okay", "ok", "busca", "perfecto", 
        "enviamelas", "con toda", "vamos", "esta bien", "hagamosle", 
        "de una", "muestrame", "dale una", "inicia", "procede", 
        "si por favor", "claro que si", "adelante", "buscalas", 
        "enseñame", "mandame", "dale pues", "hagale", "esta perfecto",
        "muestramelas", "quiero verlas", "asi es", "correcto", "estaria bien",
        "hágale", "mándame", "hágame el favor", "proceda", "vaya", "con toda",
        "dale con esa", "mándelas", "iniciemos", "hágale pues", "ya con eso",
        "esta bien", "muéstramelos", "muéstrame", "dale de una"
    ]
    
    return any(t.startswith(conf) or t == conf for conf in confirmaciones)

# =================================================================
# AGENTE PRINCIPAL
# =================================================================

def recolector_busqueda_agent(state: InmuebleState) -> InmuebleState:
    messages = ensure_list_messages(state.get("messages", []))
    ultimo_mensaje = get_last_text(messages)
    texto_lower = ultimo_mensaje.lower()

    datos = dict(state.get("datos_inmueble", {}) or {})
    busqueda_memoria = datos.get("busqueda", {})
    fase_actual = datos.get("fase_recoleccion", "")
    
    # 🔥 EXTRACCIÓN CORRECTA DEL NOMBRE DEL CLIENTE
    nombre_crudo = str(datos.get("nombre_propietario", "cliente"))
    if "|" in nombre_crudo:
        nombre_crudo = nombre_crudo.split("|")[-1].strip()
    nombre_sin_numeros = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]', '', nombre_crudo).strip()
    nombre_cliente = nombre_sin_numeros.split(" ")[0].capitalize() if nombre_sin_numeros else "cliente"
    
    ciudades_disponibles = obtener_ciudades_cobertura_sync()

    # =================================================================
    # 0. DETECTOR DE CIERRE DIRECTO GLOBAL (ZOHO INTEGRITY)
    # =================================================================
    if es_rechazo_definitivo(ultimo_mensaje, fase_actual):
        logger.info("🛑 El cliente solicitó cancelar/rechazar el proceso. Cerrando oportunidad.")
        
        motivo = "Cliente desistió o canceló la búsqueda voluntariamente"
        conclusion = "Desiste"
        
        if datos.get("esperando_ajuste"):
            motivo = datos.get("motivo_ajuste", "No cumple con las políticas")
            if "cobertura" in motivo.lower():
                conclusion = "Busca inmueble por fuera de la zona de cobertura"
            else:
                conclusion = "Busca presupuesto por debajo de la oferta"

        contexto_rechazo = (
            "El cliente indicó que no desea continuar con la búsqueda o rechazó las opciones. "
            "Despídete de forma MUY BREVE (máximo 2 oraciones), amable y profesional. "
            "Agradécele por contactar a Coninsa e indícale que quedamos a su disposición en el futuro.\n\n"
            "🚨 REGLA ESTRICTA (PENALIZACIÓN): ESTÁ TOTALMENTE PROHIBIDO sugerirle que contacte a asesores comerciales "
            "o enviarlo a la página web. Solo despídete cortésmente y termina el mensaje."
        )
        msg_cierre = generar_respuesta_contextual(
            messages, ultimo_mensaje, nombre_cliente, contexto_rechazo
        )

        datos["motivo_cierre"] = motivo
        datos["conclusion_cierre"] = conclusion
        datos["esperando_ajuste"] = False
        return {
            **state,
            "messages": messages + [msg_cierre],
            "datos_inmueble": datos,
            "operacion": "cerrar_oportunidad",
            "next_agent": "ejecutor_busqueda"
        }

    # =================================================================
    # 1. IA: EXTRACCIÓN ESTRUCTURADA
    # =================================================================
    try:
        historial_reciente = messages[-5:] if len(messages) >= 5 else messages
        resultado = extraer_datos_busqueda(historial_reciente, busqueda_memoria)

        if not resultado:
            contexto_error = (
                "Hubo un error entendiendo los datos. Pregúntale de nuevo "
                "de forma natural por su búsqueda."
            )
            msg_error = generar_respuesta_contextual(
                messages, ultimo_mensaje, nombre_cliente, contexto_error
            )
            return {
                **state,
                "messages": messages + [msg_error],
                "next_agent": "__end__"
            }

        # 🔥 PREVENCIÓN DE ERROR PYDANTIC: Valores por defecto seguros
        if getattr(resultado, 'quiere_reiniciar_busqueda', None) is None:
            resultado.quiere_reiniciar_busqueda = False

        # 🔥 ACTUALIZACIÓN DIRECTA DE ALCOBAS Y BAÑOS (Por si el cliente los cambió)
        if getattr(resultado, "numero_alcobas", None): busqueda_memoria["numero_alcobas"] = _limpiar_numero(resultado.numero_alcobas)
        if getattr(resultado, "numero_banos", None): busqueda_memoria["numero_banos"] = _limpiar_numero(resultado.numero_banos)
        if getattr(resultado, "presupuesto", None): busqueda_memoria["presupuesto"] = resultado.presupuesto

        # 🔥 LÓGICA DE MEMORIA ACUMULATIVA (Suma y resta de características)
        caract_acumuladas = str(busqueda_memoria.get("caracteristicas_deseadas", "")).lower()
        
        # A) ELIMINACIÓN: "Ya no quiero X"
        if any(neg in texto_lower for neg in ["ya no", "quitalo", "sin ", "no neces", "elimina", "quita"]):
            for palabra in ["balcon", "ascensor", "parqueadero", "mascota", "sol", "piso", "vista", "estudio", "cocina"]:
                if palabra in texto_lower:
                    caract_acumuladas = caract_acumuladas.replace(palabra, "").replace(", ,", ",").replace("  ", " ").strip(", ")

        # B) ADICIÓN: "Y que tenga Y"
        nueva_caract = resultado.caracteristicas_deseadas or ""
        if nueva_caract and nueva_caract.lower() not in caract_acumuladas:
            if caract_acumuladas: 
                caract_acumuladas = f"{caract_acumuladas}, {nueva_caract}".strip(", ")
            else: 
                caract_acumuladas = nueva_caract

        # =================================================================
        # 2. PYTHON: FUSIÓN DE MEMORIA Y SINGULARIZADOR 🔥
        # =================================================================
        
        # 🔥 EL SINGULARIZADOR: Forzamos el singular para que la API de Coninsa no falle.
        tipo_raw = (resultado.tipo_inmueble or busqueda_memoria.get("tipo_inmueble") or "Apartamento").capitalize()
        if "Apartamento" in tipo_raw: tipo_raw = "Apartamento"
        elif "Casa" in tipo_raw: tipo_raw = "Casa"
        elif "Local" in tipo_raw: tipo_raw = "Local"
        elif "Bodega" in tipo_raw: tipo_raw = "Bodega"
        elif "Oficina" in tipo_raw: tipo_raw = "Oficina"
        elif "Consultorio" in tipo_raw: tipo_raw = "Consultorio"

        memoria_actualizada = {
            "proposito": resultado.proposito or busqueda_memoria.get("proposito"),
            "departamento_ciudad": resultado.departamento_ciudad or busqueda_memoria.get("departamento_ciudad"),
            "uso_propiedad": resultado.uso_propiedad or busqueda_memoria.get("uso_propiedad"),
            "tipo_inmueble": tipo_raw, # 👈 Singularizado para la API
            "numero_alcobas": busqueda_memoria.get("numero_alcobas"),
            "numero_banos": busqueda_memoria.get("numero_banos"),
            "area_minima": getattr(resultado, 'area_minima', None) or busqueda_memoria.get("area_minima"),
            "presupuesto": busqueda_memoria.get("presupuesto"),
            "ubicacion_especifica": resultado.ubicacion_especifica or busqueda_memoria.get("ubicacion_especifica"),
            "caracteristicas_deseadas": caract_acumuladas, # 👈 AQUÍ SE GUARDAN TODOS LOS CAMBIOS
            "tiempo_literal": getattr(resultado, 'tiempo_literal', "Por definir") or busqueda_memoria.get("tiempo_literal"),
            "tiempo_necesidad": getattr(resultado, 'tiempo_necesidad', "Por definir") or busqueda_memoria.get("tiempo_necesidad"),
            "latitud": busqueda_memoria.get("latitud"),
            "longitud": busqueda_memoria.get("longitud"),
            "direccion_daxia": busqueda_memoria.get("direccion_daxia")
        }

        tipo_str = str(memoria_actualizada.get("tipo_inmueble") or "").lower()
        if any(x in tipo_str for x in ["apartamento", "casa", "apartaestudio", "apto"]):
            memoria_actualizada["uso_propiedad"] = "Vivienda"
        elif any(x in tipo_str for x in ["local", "oficina", "bodega", "consultorio"]):
            memoria_actualizada["uso_propiedad"] = "Comercio"

        tiene_coordenadas = bool(memoria_actualizada.get("latitud"))

        # =================================================================
        # 3. FILTROS DINÁMICOS (BD): COBERTURA Y TICKET MÍNIMO 
        # =================================================================
        mensaje_error = None
        motivo_ajuste = None
        lista_cob_str = quitar_tildes(ciudades_disponibles)
        ciudades_validas = [c.strip() for c in lista_cob_str.split(",") if c.strip()]

        ubi = memoria_actualizada.get("ubicacion_especifica")
        ciu = memoria_actualizada.get("departamento_ciudad")
        
        if ubi and ciu:
            if quitar_tildes(ubi) == quitar_tildes(ciu):
                memoria_actualizada["ubicacion_especifica"] = None
        elif ubi and not ciu:
            if quitar_tildes(ubi) in [quitar_tildes(c) for c in ciudades_validas]:
                memoria_actualizada["departamento_ciudad"] = ubi
                memoria_actualizada["ubicacion_especifica"] = None

        direccion_gps = memoria_actualizada.get("direccion_daxia", "")
        if tiene_coordenadas and direccion_gps:
            dir_limpia = quitar_tildes(direccion_gps)
            tiene_cobertura = any(ciudad in dir_limpia for ciudad in ciudades_validas if ciudad)

            if not tiene_cobertura:
                contexto_cob = (
                    f"El cliente envió una ubicación en '{direccion_gps}'. "
                    f"Informa que no tenemos cobertura en esa zona, solo operamos en {ciudades_disponibles}."
                )
                mensaje_error = generar_respuesta_contextual(
                    messages, ultimo_mensaje, nombre_cliente, contexto_cob
                )
                motivo_ajuste = f"Ubicación GPS fuera de cobertura: {direccion_gps}"
                memoria_actualizada["latitud"] = None
                memoria_actualizada["longitud"] = None
                memoria_actualizada["direccion_daxia"] = None

        elif memoria_actualizada.get("departamento_ciudad") and not tiene_coordenadas:
            ciudad_limpia = quitar_tildes(
                memoria_actualizada["departamento_ciudad"]
            ).split('/')[0].strip()

            if ciudad_limpia not in lista_cob_str:
                memoria_actualizada["departamento_ciudad"] = None
                contexto_cob = (
                    f"El cliente pidió {ciudad_limpia} (sin cobertura). "
                    f"Informa que solo operamos en {ciudades_disponibles} y pregunta si le sirve alguna."
                )
                mensaje_error = generar_respuesta_contextual(
                    messages, ultimo_mensaje, nombre_cliente, contexto_cob
                )
                motivo_ajuste = f"Falta de cobertura (Solicitó: {ciudad_limpia})"

        if tiene_coordenadas and memoria_actualizada.get("direccion_daxia"):
            partes_dir = [p.strip() for p in memoria_actualizada["direccion_daxia"].split(",")]
            ciudad_para_ticket = partes_dir[-1] if len(partes_dir) > 1 else partes_dir[0]
        else:
            ciudad_para_ticket = memoria_actualizada.get("departamento_ciudad", "Medellín")

        if not mensaje_error and memoria_actualizada["presupuesto"] and memoria_actualizada["proposito"]:
            val_num = _limpiar_numero(memoria_actualizada["presupuesto"])
            minimo = obtener_ticket_minimo_sync(
                memoria_actualizada["proposito"], ciudad_para_ticket
            )

            if minimo is None:
                minimo = 1200000 if "arriendo" in memoria_actualizada["proposito"].lower() else 80000000

            if 0 < val_num < minimo:
                memoria_actualizada["presupuesto"] = None
                formato_min = f"${minimo:,.0f}".replace(",", ".")
                contexto_pres = (
                    f"El presupuesto es bajo. Explica que el mínimo para "
                    f"{memoria_actualizada['proposito']} en {ciudad_para_ticket} es {formato_min} "
                    f"y pregunta si puede subirlo."
                )
                mensaje_error = generar_respuesta_contextual(
                    messages, ultimo_mensaje, nombre_cliente, contexto_pres
                )
                motivo_ajuste = f"Presupuesto inferior al mínimo (Ofreció: ${val_num:,.0f})"

        datos["busqueda"] = memoria_actualizada

        if mensaje_error:
            datos["esperando_ajuste"] = True
            datos["motivo_ajuste"] = motivo_ajuste
            return {
                **state,
                "messages": messages + [mensaje_error],
                "datos_inmueble": datos,
                "next_agent": "__end__"
            }

        datos["esperando_ajuste"] = False

        # =================================================================
        # 4. POLICÍA DE DATOS FALTANTES E INTELIGENCIA GEOGRÁFICA 🔥
        # =================================================================
        dato_falta = None
        
        # A) Interrupción por cambio de barrio o pregunta de disponibilidad prematura
        pregunta_disp = any(x in texto_lower for x in ["tienes en", "tienen en", "hay en", "buscame en"])
        ubi_extraida = resultado.ubicacion_especifica
        ubi_memoria = busqueda_memoria.get("ubicacion_especifica")
        ciu_memoria = busqueda_memoria.get("departamento_ciudad", "")
        
        if (ubi_extraida and ubi_memoria and (quitar_tildes(ubi_extraida) != quitar_tildes(ubi_memoria))) or (pregunta_disp and not ubi_memoria):
            lugar_nuevo = ubi_extraida if ubi_extraida else "ese lugar"
            instruccion = (
                f"Dile LITERALMENTE: 'Oye {nombre_cliente}, entiendo que quieres buscar en {lugar_nuevo}. "
                f"Por ahora no te puedo confirmar disponibilidad allí porque primero necesito recolectar todos los datos para que el sistema filtre bien. "
                f"¿Me confirmas entonces en qué barrio y en qué ciudad quieres que hagamos la búsqueda definitiva?'"
            )
            pregunta = generar_respuesta_contextual(messages, ultimo_mensaje, nombre_cliente, instruccion)
            return {**state, "messages": messages + [pregunta], "datos_inmueble": datos, "next_agent": "__end__"}

        # B) Cascada de recolección (ORDEN INNEGOCIABLE)
        if not memoria_actualizada.get("proposito"):
            barrio_act = memoria_actualizada.get("ubicacion_especifica")
            if barrio_act:
                dato_falta = f"Dile LITERALMENTE: 'Vale, {barrio_act} es un barrio muy buscado, pero para continuar me puedes decir si buscas arrendar o comprar?'"
            else:
                dato_falta = "pregúntale si está interesado en arrendar o comprar."
        
        elif not memoria_actualizada.get("departamento_ciudad") and not tiene_coordenadas:
            barrio_act = memoria_actualizada.get("ubicacion_especifica")
            if barrio_act:
                dato_falta = f"Dile LITERALMENTE: 'Me mencionaste el barrio {barrio_act}, pero para buscar necesito que me confirmas la ciudad. ¿Buscas en Bogotá, Medellín o Barranquilla?'"
            else:
                dato_falta = "pregúntale en qué ciudad busca (Bogotá, Medellín o Barranquilla)."

        elif not memoria_actualizada.get("ubicacion_especifica") and not tiene_coordenadas:
            ciu_act = memoria_actualizada.get('departamento_ciudad')
            dato_falta = f"dile que para buscar en {ciu_act} necesitas saber obligatoriamente en qué barrio o sector quiere vivir."

        elif not memoria_actualizada.get("presupuesto"):
            dato_falta = "pregúntale cuál es su presupuesto máximo mensual."

        elif not memoria_actualizada.get("caracteristicas_deseadas"):
            dato_falta = "pregúntale qué características son indispensables para el inmueble (ej. habitaciones, baños, parqueadero)."

        # 🔥 LA LISTA DE DÍAS RECUPERADA 🔥
        elif not memoria_actualizada.get("tiempo_necesidad") or memoria_actualizada.get("tiempo_necesidad") == "Por definir":
            dato_falta = (
                "pregúntale para cuándo necesita mudarse"
            )

        if dato_falta:
            instruccion_final = f"Tarea: {dato_falta}. Responde de forma corta, conversacional y sin inventar proyectos."
            pregunta = generar_respuesta_contextual(messages, ultimo_mensaje, nombre_cliente, instruccion_final)
            return {
                **state,
                "messages": messages + [pregunta],
                "datos_inmueble": datos,
                "next_agent": "__end__"
            }

        # =================================================================
        # 5. EL FLUJO PERFECTO DE CONFIRMACIÓN Y BÚSQUEDA 🔥
        # =================================================================
        ya_busco_antes = datos.get("nota_inicial_creada", False)
        es_arriendo = (memoria_actualizada.get("proposito") or "").lower() == "arriendo"
        
        texto_limpio_conf = re.sub(r'[^a-záéíóúñ\s]', '', ultimo_mensaje.lower()).strip()
        es_afirm = es_afirmacion_busqueda(ultimo_mensaje)

        # Si responde "No más" o "Así está bien" en ajustes, es un SI para avanzar
        if fase_actual == "mas_ajustes" and texto_limpio_conf in ["no", "nop", "ninguno", "nomas", "ya", "asi", "asi esta bien"]:
            es_afirm = True

        t = memoria_actualizada.get('tipo_inmueble', 'Apartamento')
        u = memoria_actualizada.get('ubicacion_especifica', 'la zona')
        c = memoria_actualizada.get('caracteristicas_deseadas', 'sus necesidades')
        d = memoria_actualizada.get('departamento_ciudad', '')

        # --- ESCENARIO A: PRIMERA BÚSQUEDA (CON REQUISITOS) ---
        if not ya_busco_antes:
            if not datos.get("puente_requisitos_mostrado"):
                # PASO 1: Mostrar requisitos por primera vez
                datos["puente_requisitos_mostrado"] = True
                datos["esperando_confirmacion_codeudor"] = True if es_arriendo else False
                datos["esperando_confirmacion_pago"] = False if es_arriendo else True
                
                req_txt = "requerimos codeudor y demostrar ingresos" if es_arriendo else "¿comprarás con recursos propios o crédito?"
                contexto_req = f"Resumen: buscamos {t} en {u}. Dile: '{req_txt}. Iniciaré la búsqueda con esto, ¿estás de acuerdo?'"
                res = generar_respuesta_contextual(messages, ultimo_mensaje, nombre_cliente, contexto_req)
                return { **state, "messages": messages + [res], "datos_inmueble": datos, "next_agent": "__end__" }

            else:
                # PASO 2: El usuario ya dijo "Sí" a los requisitos
                if es_afirm:
                    datos["nota_inicial_creada"] = True
                    datos["esperando_confirmacion_codeudor"] = False
                    datos["esperando_confirmacion_pago"] = False
                    datos["esperando_entrega_tarjetas"] = True # 👈 IMPORTANTE PARA EL ROUTER
                    
                    contexto_exito = f"Dile con entusiasmo:  {nombre_cliente} Ya me pongo a buscar. ¿Deseas ver las opciones apenas las tenga?'"
                    res = generar_respuesta_contextual(messages, ultimo_mensaje, nombre_cliente, contexto_exito)
                    return { **state, "messages": messages + [res], "datos_inmueble": datos, "operacion": "busqueda_validar", "next_agent": "ejecutor_busqueda" }

        # --- ESCENARIO B: AJUSTES (TU FLUJO DE 3 PASOS) ---
        else:
            # Si el usuario detectó que quiere un cambio y estaba en confirmar_inicio
            if fase_actual == "confirmar_inicio" and not es_afirm:
                fase_actual = "mas_ajustes" 
                datos["fase_recoleccion"] = "mas_ajustes"

            if fase_actual == "confirmar_inicio":
                if es_afirm:
                    # PASO FINAL: Confirmó el resumen y que inicie
                    datos["fase_recoleccion"] = ""
                    datos["esperando_entrega_tarjetas"] = True
                    contexto_final = f"Dile: '¡Listo {nombre_cliente}! Ya tengo todo preparado con estos ajustes. ¿Quieres ver las opciones?'"
                    res = generar_respuesta_contextual(messages, ultimo_mensaje, nombre_cliente, contexto_final)
                    return { **state, "messages": messages + [res], "datos_inmueble": datos, "operacion": "busqueda_validar", "next_agent": "ejecutor_busqueda" }
            
            elif fase_actual == "mas_ajustes":
                if es_afirm:
                    # PASO 2: Dijo "No más ajustes" o "Así está bien". Preguntamos para iniciar
                    datos["fase_recoleccion"] = "confirmar_inicio"
                    resumen = f"{t} en {u}, presupuesto {memoria_actualizada.get('presupuesto')}, con {c}"
                    contexto_conf = f"Dile: 'Vale, entonces buscaré: {resumen}. ¿Es correcto para iniciar la búsqueda?'"
                    res = generar_respuesta_contextual(messages, ultimo_mensaje, nombre_cliente, contexto_conf)
                    return { **state, "messages": messages + [res], "datos_inmueble": datos, "next_agent": "__end__" }

            # PASO 1: Acaba de pedir un cambio o sigue en el bucle de cambios
            datos["fase_recoleccion"] = "mas_ajustes"
            contexto_ajuste = f"Repasa: {t} en {u} con {c}. Pregunta: '¿Deseas realizar algún otro ajuste o ya con eso está bien para buscar?'"
            res = generar_respuesta_contextual(messages, ultimo_mensaje, nombre_cliente, contexto_ajuste)
            return { **state, "messages": messages + [res], "datos_inmueble": datos, "next_agent": "__end__" }

    except Exception as e:
        logger.error(f"Error en recolector: {e}")
        return {**state, "next_agent": "__end__"}

__all__ = ["recolector_busqueda_agent"]