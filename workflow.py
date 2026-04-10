# app/workflow.py
from langgraph.graph import StateGraph, END
from app.state import InmuebleState
from app.agents import (
    validacion_telefono_agent,
    agente_politica,
    router_agent,
    bienvenida_agent,
    recolector_identidad_agent,
    ejecutor_identidad_agent,
    recolector_busqueda_agent, 
    ejecutor_busqueda_agent,
)
from app.types import AgentName


def build_app():
    workflow = StateGraph(InmuebleState)

    # ==========================================================
    # NODOS (Las estaciones del flujo)
    # ==========================================================
    workflow.add_node(AgentName.VALIDACION_TELEFONO.value, validacion_telefono_agent)
    workflow.add_node(AgentName.AGENTE_POLITICA.value, agente_politica)
    workflow.add_node(AgentName.BIENVENIDA.value, bienvenida_agent)
    workflow.add_node(AgentName.ROUTER.value, router_agent)
    workflow.add_node(AgentName.RECOLECTOR_IDENTIDAD.value, recolector_identidad_agent)
    workflow.add_node(AgentName.EJECUTOR_IDENTIDAD.value, ejecutor_identidad_agent)
    
    # NODOS DE BÚSQUEDA
    workflow.add_node("recolector_busqueda", recolector_busqueda_agent) 
    workflow.add_node("ejecutor_busqueda", ejecutor_busqueda_agent) 

    # ==========================================================
    # RUTAS (Los rieles de la carretera)
    # ==========================================================
    
    # Inicio: Siempre validamos el teléfono primero
    workflow.set_entry_point(AgentName.VALIDACION_TELEFONO.value)

    # De Validación siempre vamos al Router para decidir qué sigue
    workflow.add_edge(AgentName.VALIDACION_TELEFONO.value, AgentName.ROUTER.value)

    # RUTA DE POLÍTICA
    workflow.add_conditional_edges(
        AgentName.AGENTE_POLITICA.value,
        lambda state: state.get("next_agent", AgentName.END.value),
        {
            AgentName.ROUTER.value: AgentName.ROUTER.value,
            AgentName.BIENVENIDA.value: AgentName.BIENVENIDA.value,
            AgentName.AGENTE_POLITICA.value: AgentName.AGENTE_POLITICA.value,
            AgentName.END.value: END,
        },
    )

    # RUTA DEL ROUTER (El controlador de tráfico)
    workflow.add_conditional_edges(
        AgentName.ROUTER.value,
        lambda state: state.get("next_agent", AgentName.END.value),
        {
            AgentName.ROUTER.value: AgentName.ROUTER.value,
            AgentName.RECOLECTOR_IDENTIDAD.value: AgentName.RECOLECTOR_IDENTIDAD.value,
            "recolector_busqueda": "recolector_busqueda", 
            "ejecutor_busqueda": "ejecutor_busqueda", # Para clics rápidos en códigos
            AgentName.BIENVENIDA.value: AgentName.BIENVENIDA.value,
            AgentName.AGENTE_POLITICA.value: AgentName.AGENTE_POLITICA.value,
            AgentName.EJECUTOR_IDENTIDAD.value: AgentName.EJECUTOR_IDENTIDAD.value,
            AgentName.END.value: END,
        },
    )

    # Bienvenida: Solo saluda y termina el turno del bot
    workflow.add_edge(AgentName.BIENVENIDA.value, END)

    # RUTA DE IDENTIDAD (Recolección)
    workflow.add_conditional_edges(
        AgentName.RECOLECTOR_IDENTIDAD.value,
        lambda state: state.get("next_agent", AgentName.END.value),
        {
            AgentName.EJECUTOR_IDENTIDAD.value: AgentName.EJECUTOR_IDENTIDAD.value,
            AgentName.ROUTER.value: AgentName.ROUTER.value,
            AgentName.END.value: END,
        },
    )

    # RUTA DE IDENTIDAD (Ejecución/Zoho)
    workflow.add_conditional_edges(
        AgentName.EJECUTOR_IDENTIDAD.value,
        lambda state: state.get("next_agent", AgentName.END.value),
        {
            AgentName.ROUTER.value: AgentName.ROUTER.value,
            AgentName.RECOLECTOR_IDENTIDAD.value: AgentName.RECOLECTOR_IDENTIDAD.value,
            "recolector_busqueda": "recolector_busqueda", 
            AgentName.END.value: END,
        },
    )

    # RUTA DE BÚSQUEDA (Recolección)
    workflow.add_conditional_edges(
        "recolector_busqueda",
        lambda state: state.get("next_agent", AgentName.END.value),
        {
            "ejecutor_busqueda": "ejecutor_busqueda", # Salta a buscar si tiene todo o si es cierre
            AgentName.END.value: END,               # Termina si faltan datos
        },
    )
    
    # El Ejecutor de Búsqueda siempre termina el turno tras enviar las tarjetas o cerrar el Deal
    workflow.add_edge("ejecutor_busqueda", END) 

    return workflow.compile()


app = build_app()