# app/utils/intent.py
from __future__ import annotations
import re
import json 
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator
from langchain_core.prompts import ChatPromptTemplate
from app.config import get_llm

# ==================================================
# ESQUEMAS DE EXTRACCIÓN (SCHEMAS)
# ==================================================

class IntentOut(BaseModel):
    accion: Literal["registro", "actualizar_datos", "consulta", "ayuda", "busqueda", "ninguno"] = Field(
        description="Intención general. 'busqueda' SOLO si menciona interés explícito en inmuebles."
    )
    negocio: Optional[Literal["arriendo", "venta"]] = Field(
        default=None, 
        description="DEBE SER 'arriendo' o 'venta'. Convierte verbos automáticamente: 'arrendar'/'alquilar' = 'arriendo', 'comprar' = 'venta'."
    )

class ConfirmacionIdentidadOut(BaseModel):
    confirma_datos: bool = Field(description="True si confirma que los datos están OK.", default=False)

class PoliticaOut(BaseModel):
    decision: Literal["acepta", "rechaza", "pregunta", "ninguno"] = Field(description="Clasificación de política.")
    motivo: Optional[str] = None

class IdentidadYPropositoOut(BaseModel):
    actualizar_nombre: bool = Field(default=False)
    nuevo_nombre: Optional[str] = None
    actualizar_email: bool = Field(default=False)
    nuevo_email: Optional[str] = None
    actualizar_cedula: bool = Field(default=False)
    nueva_cedula: Optional[str] = None
    quiere_actualizar_datos_generico: bool = Field(default=False)
    quiere_ver_datos: bool = Field(default=False)
    quiere_cambiar_celular: bool = Field(default=False)
    proposito: Literal["registro", "busqueda", "ninguno"] = Field(default="ninguno")
    
    negocio_detectado: Optional[Literal["Arriendo", "Venta"]] = Field(
        default=None, 
        description="DEBE SER ESTRICTAMENTE 'Arriendo' o 'Venta'."
    )
    tipo_inmueble_detectado: Optional[str] = Field(default=None, description="Ej: apartamento, casa, local, bodega.")
    ciudad_detectada: Optional[str] = Field(default=None, description="Ciudad o municipio (Ej: Medellín, Bogotá, Bucaramanga, Barranquilla).")
    barrio_detectado: Optional[str] = Field(default=None, description="Barrio o sector específico (Ej: Laureles, El Poblado).")
    presupuesto_detectado: Optional[str] = Field(default=None, description="Presupuesto numérico si el usuario lo menciona de entrada.")
    caracteristicas_detectadas: Optional[str] = Field(default=None, description="Características del inmueble (ej: con balcón, vista al parque).")
    
    alcobas_detectadas: Optional[int] = Field(default=None, description="Número de alcobas si el usuario lo menciona.")
    tiempo_detectado: Optional[str] = Field(default=None, description="Para cuándo necesita el inmueble (ej: 'en 3 meses', 'inmediatamente').")

    @field_validator("nueva_cedula", mode="before")
    @classmethod
    def limpiar_cedula(cls, v):
        if v and isinstance(v, str):
            return re.sub(r"\D", "", v)
        return v

_llm = get_llm()

# ==================================================
# HELPERS
# ==================================================
def _ultimo_texto(mensajes) -> str:
    if not mensajes: return ""
    if isinstance(mensajes, str): return mensajes
    ultimo = mensajes[-1]
    if hasattr(ultimo, 'content'): return ultimo.content
    if isinstance(ultimo, dict) and "content" in ultimo: return ultimo["content"]
    return str(ultimo)

def _formatear_historial(mensajes) -> str:
    if not mensajes or isinstance(mensajes, str):
        return "Sin historial previo."
    lineas = []
    for m in mensajes[:-1]: 
        rol = "Usuario" if (hasattr(m, 'type') and m.type == "human") or (isinstance(m, dict) and m.get("role") == "user") else "Asistente"
        contenido = m.content if hasattr(m, 'content') else (m.get('content', '') if isinstance(m, dict) else str(m))
        lineas.append(f"{rol}: {contenido}")
    return "\n".join(lineas[-4:])

def _formatear_historial_completo(mensajes) -> str:
    if not mensajes or isinstance(mensajes, str):
        return str(mensajes)
    lineas = []
    for m in mensajes[-6:]: 
        rol = "Usuario" if (hasattr(m, 'type') and m.type == "human") or (isinstance(m, dict) and m.get("role") == "user") else "Asistente"
        contenido = m.content if hasattr(m, 'content') else (m.get('content', '') if isinstance(m, dict) else str(m))
        lineas.append(f"{rol}: {contenido}")
    return "\n".join(lineas)

# ==================================================
# PROMPTS
# ==================================================

_IDENTIDAD_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """Eres un motor de extracción de datos en formato JSON. Tu único objetivo es leer el historial y extraer los datos solicitados. No redactes respuestas.

        REGLAS DE ORO DE EXTRACCIÓN:
        1. COMPARACIÓN CON MEMORIA: Si el usuario duda de un dato que YA TIENES en la memoria actual, marca 'quiere_ver_datos' = True.
        2. EXTRACCIÓN TOTAL TEMPRANA: Si el usuario envía un mensaje largo con lo que busca, debes extraer ABSOLUTAMENTE TODOS LOS DATOS de una vez (Ciudad, barrio, presupuesto, características, alcobas, tiempo, negocio).
        3. 🚨 REGLA DE PRESUPUESTO: Si el cliente da un rango numérico (ej: "de 3 a 3.5" o "entre 2 y 5"), asume siempre el valor MÁS ALTO y escríbelo en números completos. Entiende abreviaciones como "3m", "3.5" o "3 millones" como cifras en millones de pesos (ej: 3500000).
        
        HISTORIAL: {historial}
        DATOS_ACTUALES:
        Nombre: {nombre_actual} | Email: {email_actual} | Cédula: {cedula_actual}"""
    ),
    ("human", "{texto_usuario}")
])

def interpretar_identidad_y_proposito(mensajes_o_texto: str | list, nombre_actual: str = "", telefono_actual: str = "", email_actual: str = "", cedula_actual: str = "") -> IdentidadYPropositoOut:
    texto = mensajes_o_texto if isinstance(mensajes_o_texto, str) else _ultimo_texto(mensajes_o_texto)
    historial = "Sin historial." if isinstance(mensajes_o_texto, str) else _formatear_historial(mensajes_o_texto)
    chain = _IDENTIDAD_PROMPT | _llm.with_structured_output(IdentidadYPropositoOut)
    return chain.invoke({
        "texto_usuario": texto,
        "historial": historial,
        "nombre_actual": nombre_actual or "No registrado",
        "email_actual": email_actual or "No registrado",
        "cedula_actual": cedula_actual or "No registrado",
    })

_PROMPT_INTENT = ChatPromptTemplate.from_messages([
    (
        "system",
        """Define la intención: 'registro' si el usuario da datos personales, 'busqueda' si el usuario pide inmuebles. 
        HISTORIAL: {historial}"""
    ),
    ("human", "{texto}")
])

def clasificar_intencion(mensajes_o_texto: str | list) -> IntentOut:
    texto = mensajes_o_texto if isinstance(mensajes_o_texto, str) else _ultimo_texto(mensajes_o_texto)
    historial = "Sin historial." if isinstance(mensajes_o_texto, str) else _formatear_historial(mensajes_o_texto)
    chain = _PROMPT_INTENT | _llm.with_structured_output(IntentOut)
    return chain.invoke({"texto": texto, "historial": historial})

def clasificar_confirmacion_identidad(texto: str) -> ConfirmacionIdentidadOut:
    return (_llm.with_structured_output(ConfirmacionIdentidadOut)).invoke(texto)

def clasificar_politica(texto: str, contexto: str = "") -> PoliticaOut:
    return (_llm.with_structured_output(PoliticaOut)).invoke(f"Contexto: {contexto}. Texto: {texto}")

# ==================================================
# EXTRACCIÓN DE BÚSQUEDA (CON REINICIO Y POLICÍA GEO 🧠)
# ==================================================

class DatosBusquedaOut(BaseModel):
    proposito: Optional[Literal["Arriendo", "Venta"]] = Field(
        default=None,
        description="El tipo de transacción. DEBE SER ESTRICTAMENTE 'Arriendo' o 'Venta'."
    )
    departamento_ciudad: Optional[str] = Field(default=None, description="Ciudad/Departamento principal.")
    uso_propiedad: Optional[str] = Field(default=None, description="Vivienda o Comercio.")
    tipo_inmueble: Optional[str] = Field(default=None, description="Apartamento, Casa, etc.")
    numero_alcobas: Optional[int] = Field(default=None, description="Número de alcobas. DEBE SER UN NÚMERO ENTERO (ej: 3).")
    numero_banos: Optional[int] = Field(default=None, description="Número de baños si el usuario lo menciona explícitamente. DEBE SER UN NÚMERO ENTERO (ej: 2).")
    area_minima: Optional[float] = Field(default=None, description="Área o metros cuadrados mínimos si el usuario lo menciona (ej: 60). DEBE SER UN NÚMERO DECIMAL O ENTERO.")
    presupuesto: Optional[str] = Field(default=None, description="Presupuesto numérico.")
    metodo_pago: Optional[str] = Field(default=None, description="Solo para venta.")
    ubicacion_especifica: Optional[str] = Field(default=None, description="Barrio o sector específico.")
    caracteristicas_deseadas: Optional[str] = Field(default=None, description="Relato completo de detalles y el sueño del cliente.")
    quiere_reiniciar_busqueda: bool = Field(
        default=False, 
        description="True si el cliente indica que NO le gustó nada de lo anterior, quiere buscar en otro lugar/ciudad o quiere algo totalmente distinto."
    )
    tiempo_literal: Optional[str] = Field(
        default="Por definir", 
        description="Lo que el usuario dijo exactamente sobre el tiempo (ej: '31 de marzo', 'en unos 15 días')."
    )
    tiempo_necesidad: Optional[Literal[
        "Inmediatamente", "De 8 a 15 días", "De 15 a 30 días", 
        "De 1 a 2 meses", "De 2 a 4 meses", "De 4 a 6 meses", 
        "De 6 a 12 meses", "Más de 1 año", "Por definir"
    ]] = Field(
        default="Por definir", 
        description="Homologación estricta."
    )
    
    @field_validator("tiempo_necesidad", mode="before")
    @classmethod
    def blindaje_tiempo(cls, v):
        if not v: 
            return "Por definir"
        v_str = str(v).lower()
        if "3 meses" in v_str:
            return "De 2 a 4 meses"
        opciones_validas = [
            "Inmediatamente", "De 8 a 15 días", "De 15 a 30 días", 
            "De 1 a 2 meses", "De 2 a 4 meses", "De 4 a 6 meses", 
            "De 6 a 12 meses", "Más de 1 año", "Por definir"
        ]
        if v not in opciones_validas:
            return "Por definir"
        return v

    @field_validator("numero_alcobas", "numero_banos", mode="before")
    @classmethod
    def blindaje_numeros(cls, v):
        if v is None: return None
        if isinstance(v, int): return v
        numeros = re.findall(r'\d+', str(v))
        if numeros: return int(numeros[0])
        mapa = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6}
        return mapa.get(str(v).lower().strip(), None)

    @field_validator("proposito", mode="before")
    @classmethod
    def blindaje_proposito(cls, v):
        if not v: return None
        val_limpio = str(v).lower()
        if "arriend" in val_limpio or "alquil" in val_limpio or "rent" in val_limpio:
            return "Arriendo"
        if "vent" in val_limpio or "compr" in val_limpio:
            return "Venta"
        return v

# 🔥 PROMPT REFORZADO: PRESUPUESTO Y CARACTERÍSTICAS ACUMULATIVAS 🔥
_BUSQUEDA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """Eres un motor de procesamiento de datos NLP. Tu único objetivo es leer el mensaje del usuario y extraer los datos técnicos de búsqueda inmobiliaria en formato JSON.
    NO debes redactar respuestas conversacionales ni asumir el rol de un humano.

    🚨 REGLA DE POLICÍA GEOGRÁFICA:
    - Diferencia CIUDAD de BARRIO. (Ej: Medellín es ciudad, Laureles es barrio).
    - Si el usuario menciona una ubicación, clasifícala correctamente. No mezcles.

    🚨 REGLA DE PRESUPUESTO: 
    - Si el cliente da un rango (ej: "de 3 a 3.5" o "entre 2 y 5 millones"), asume siempre el valor MÁS ALTO. 
    - Entiende que "m", "millones" o decimales como "3.5" significa millones. Devuelve la cifra en números completos (ej: 3500000).

    🚨 REGLA DE REINICIO (BORRÓN Y CUENTA NUEVA):
    - Si el cliente dice: 'no me gustó ninguno', 'quiero buscar otro apartamento en otra ciudad', 'nada de eso me sirve', marca 'quiere_reiniciar_busqueda' = True.
    - OJO: Si dice "lo mismo pero en otra ciudad", NO es un reinicio total. Mantiene las características y solo cambia la ciudad.

    🚨 REGLA DE CARACTERÍSTICAS (ACUMULATIVO Y OBLIGATORIO): 
    - El campo 'caracteristicas_deseadas' NO SE REEMPLAZA, SE ACUMULA. Si en la MEMORIA ACTUAL ya dice "balcón", y en el historial el usuario pide "ascensor", DEBES devolver "balcón, ascensor". 
    - Solo elimina una característica si el usuario dice explícitamente 'ya no quiero X'. ¡NUNCA borres lo que ya está en la memoria a menos que te lo pidan!

    📅 ATENCIÓN, LA FECHA ACTUAL ES: {fecha_actual}
    
    🚨 INSTRUCCIÓN MATEMÁTICA PARA EL TIEMPO:
    Cuando el cliente te dé una fecha (ej: 'noviembre', '13 de julio', 'finales de este mes'), calcula mentalmente la diferencia entre la FECHA ACTUAL y la fecha solicitada:
    - De 0 a 7 días -> Usa "Inmediatamente"
    - De 8 a 15 días -> Usa "De 8 a 15 días"
    - De 16 a 30 días -> Usa "De 15 a 30 días"
    - De 1 a 2 meses -> Usa "De 1 a 2 meses"
    - De 2 a 4 meses -> Usa "De 2 a 4 meses"
    - De 4 a 6 meses -> Usa "De 4 a 6 meses"
    - De 6 a 12 meses -> Usa "De 6 a 12 meses"
    - Más de 1 año -> Usa "Más de 1 año"
    - Si el cliente no sabe o no dice -> Usa "Por definir"

    ⚠️ MEMORIA ACTUAL (Lo que el cliente ya te dijo previamente): 
    {datos_actuales}
    
    🚨 REGLAS ESTRICTAS DE EXTRACCIÓN:
    1. Analiza el HISTORIAL RECIENTE completo para entender qué cambios quiere hacer el usuario.
    2. Si el usuario menciona que busca 'apartamento', 'casa' o 'finca', asume que el uso de la propiedad es 'Vivienda'.
    3. Si menciona un sector o barrio específico, guárdalo en 'ubicacion_especifica'.
    """),
    ("human", "HISTORIAL RECIENTE COMPLETO DE AJUSTES:\n{historial_completo}")
])

def extraer_datos_busqueda(mensajes_o_texto: str | list, datos_actuales: dict) -> DatosBusquedaOut:
    historial_completo = _formatear_historial_completo(mensajes_o_texto) if isinstance(mensajes_o_texto, list) else mensajes_o_texto
    
    chain = _BUSQUEDA_PROMPT | _llm.with_structured_output(DatosBusquedaOut)
    
    hoy = datetime.now().strftime("%d de %B de %Y") 
    
    return chain.invoke({
        "fecha_actual": hoy,
        "datos_actuales": json.dumps(datos_actuales, ensure_ascii=False),
        "historial_completo": historial_completo
    })

__all__ = [
    "IntentOut", "PoliticaOut", "IdentidadYPropositoOut", "ConfirmacionIdentidadOut", "DatosBusquedaOut",
    "clasificar_confirmacion_identidad", "clasificar_intencion", "clasificar_politica", "interpretar_identidad_y_proposito", "extraer_datos_busqueda"
]