# app/utils/evaluador_inmuebles.py
import logging
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from app.config import get_llm

logger = logging.getLogger("EVALUADOR_LLM")

# Inicializamos el LLM usando la configuración central (¡Ojo a los paréntesis!)
llm_evaluador = get_llm()

def evaluar_descripciones_con_llm(inmuebles: list, caracteristicas_deseadas: str) -> dict:
    """
    Usa el LLM para leer las descripciones.
    Devuelve un diccionario con:
    - 'ids_cumplen': Los que sí tienen la característica.
    - 'ids_rechazados': Los que NIEGAN EXPLÍCITAMENTE tener la característica.
    """
    if not caracteristicas_deseadas or not inmuebles:
        return {"ids_cumplen": [], "ids_rechazados": []}

    # 1. Preparamos los textos para el LLM
    textos_inmuebles = ""
    for inm in inmuebles:
        inm_id = str(inm.get("id", inm.get("codigo_abr_inmueble", "N/A")))
        
        desc = ""
        if isinstance(inm.get("descripcion_inmueble"), dict):
             desc = inm["descripcion_inmueble"].get("value", "")
        
        obs = ""
        if isinstance(inm.get("observaciones"), dict):
             obs = inm["observaciones"].get("value", "")
             
        texto_completo = f"{desc} {obs}".strip()
        if not texto_completo:
             texto_completo = str(inm.get("descripcion", "")) + " " + str(inm.get("observacion", ""))

        textos_inmuebles += f"ID: {inm_id}\nDescripción: {texto_completo}\n\n"

    # 2. Definimos el formato JSON esperado
    parser = JsonOutputParser()
    
    # 3. El Prompt Estricto (¡Ahora con veto!)
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", "Eres un asistente inmobiliario experto evaluando descripciones de propiedades. "
                   "Se te dará una lista de inmuebles (con su ID y descripción) y las características especiales que busca el cliente. "
                   "Tu tarea es clasificar los IDs en DOS listas:\n"
                   "1. 'ids_cumplen': Inmuebles donde la descripción indica que SÍ posee esas características o se acerca mucho (ej. 'Pet friendly' = acepta mascotas).\n"
                   "2. 'ids_rechazados': Inmuebles donde la descripción EXPLÍCITAMENTE NIEGA lo que el cliente pide (ej. el cliente pide parqueadero/garaje y la descripción dice 'no cuenta con garaje' o 'sin garaje', o pide ascensor y dice 'no tiene ascensor').\n"
                   "Si la descripción no menciona nada al respecto, no lo pongas en ninguna de las dos listas.\n"
                   "Ignora detalles como el número de alcobas o precio, concéntrate ÚNICAMENTE en las características especiales solicitadas.\n\n"
                   "Instrucciones de salida:\n{format_instructions}"),
        ("user", "Características buscadas por el cliente: '{caracteristicas_deseadas}'\n\n"
                 "Lista de inmuebles a evaluar:\n{textos_inmuebles}\n\n"
                 "Devuelve un JSON estrictamente con esta estructura:\n"
                 "{{\n"
                 "  \"ids_cumplen\": [\"ID1\", \"ID2\"], \n"
                 "  \"ids_rechazados\": [\"ID3\"] \n"
                 "}}")
    ])

    try:
        cadena = prompt_template | llm_evaluador | parser
        resultado = cadena.invoke({
            "caracteristicas_deseadas": caracteristicas_deseadas,
            "textos_inmuebles": textos_inmuebles,
            "format_instructions": parser.get_format_instructions()
        })
        
        return {
            "ids_cumplen": resultado.get("ids_cumplen", []),
            "ids_rechazados": resultado.get("ids_rechazados", [])
        }
    except Exception as e:
        logger.error(f"❌ Error evaluando descripciones con LLM: {e}")
        return {"ids_cumplen": [], "ids_rechazados": []}