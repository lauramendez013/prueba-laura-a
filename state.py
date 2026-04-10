# app/state.py
from typing import List, Dict, Any, Optional
from typing_extensions import TypedDict

class InmuebleState(TypedDict, total=False):
    # ==================================================
    # CORE / CONTROL DE FLUJO
    # ==================================================
    messages: List[Any]
    operacion: str
    next_agent: str
    session_id: str
    modo: Optional[str]
    
    # ==================================================
    # DATOS DE NEGOCIO (LA MALETA FLEXIBLE)
    # ==================================================
    datos_inmueble: Dict[str, Any]  
    resultado: Dict[str, Any]
    plantilla_twilio: Optional[Dict[str, Any]]

    # ==================================================
    # IDENTIDAD Y CONTACTO BASE
    # ==================================================
    user_id: Optional[str]
    sender_id: Optional[str]
    user_phone: Optional[str]
    phone: Optional[str]
    telefono: Optional[str]

    # ==================================================
    # POLÍTICA DE DATOS
    # ==================================================
    politica_mostrada: bool
    politica_aceptada: bool
    politica_rechazada_previa: bool

    # ==================================================
    # CRM / ESTADO DE VALIDACIÓN
    # ==================================================
    cliente_existente: bool
    tipo_cliente: str
    identidad_completa: bool
    telefono_validado: bool
    zoho_id: Optional[str]
    crm_error: bool
    api_error: bool

    # ==================================================
    # ESTADOS DEL DIÁLOGO (FLAGS)
    # ==================================================
    bienvenida_dada: bool
    confirmando_nombre: bool
    confirmando_celular: bool
    esperando_nombre: bool
    esperando_celular: bool
    solicitud_registro_enviada: bool

    # Propósito
    preguntando_proposito: bool
    confirmando_proposito: bool
    proposito: Optional[str]
    proposito_usuario: Optional[str]