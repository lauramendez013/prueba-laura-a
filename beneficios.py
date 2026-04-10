# app/tools/beneficios.py
from typing import Literal

Prop = Literal["arriendo", "venta"]

_BENEFICIOS = {
    "arriendo": (
        "**Beneficios al CONSIGNAR para ARRIENDO**\n\n"
        "**VIVIENDA**\n"
        "- Pago garantizado del arriendo, incluso si el inquilino no paga.\n"
        "- Tarifas de administración según el día de pago.\n"
        "- Cobertura en daños, servicios y faltantes (según amparo adicional).\n"
        "- Asistencia en emergencias.\n"
        "- Publicación en portales inmobiliarios.\n"
        "- Pago del canon dentro del mes vigente.\n"
        "- Firma electrónica y gestión 100% digital.\n"
        "- Recuperación del inmueble en caso de incumplimiento.\n"
        "- Análisis de riesgo al arrendatario.\n\n"
        "**COMERCIO**\n"
        "- Variedad de tarifas según el valor del inmueble.\n"
        "- Estudio de mercado gratuito.\n"
        "- Publicación en portales para encontrar arrendatario ideal.\n"
        "- Conexión con marcas consolidadas del país.\n"
        "- Firma electrónica y procesos digitales.\n"
        "- Pago de arriendo garantizado aunque no pague.\n"
        "- Respaldo jurídico.\n"
        "- Publicidad y mercadeo sin costo."
    ),
    "venta": (
        "**Beneficios al CONSIGNAR para VENTA**\n"
        "- Acompañamiento de principio a fin.\n"
        "- Estudio de mercado gratuito.\n"
        "- Publicación en portales aliados.\n"
        "- Publicidad y mercadeo sin costo.\n"
        "- Ruta de venta según tu meta.\n"
        "- Orientación jurídica hasta la firma de escrituras.\n"
        "- Validación SARLAFT y SAGRILAFT.\n"
        "- Compraventa segura y respaldada por expertos."
    ),
}

def obtener_beneficios_propietario(tipo: Prop) -> str:
    tipo = "arriendo" if tipo == "arriendo" else "venta"
    return _BENEFICIOS[tipo]
