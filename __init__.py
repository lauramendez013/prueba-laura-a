from .recolector_identidad import recolector_identidad_agent
# from .recolector_registro  import recolector_registro_agent
# from .recolector_consulta  import recolector_consulta_agent
from .ejecutor_identidad   import ejecutor_identidad_agent
# from .ejecutor_registro    import ejecutor_registro_agent
# from .ejecutor_consulta    import ejecutor_consulta_agent
from .router               import router_agent
from .bienvenida           import bienvenida_agent
from .politica             import agente_politica
from .validacion_telefono  import validacion_telefono_agent
from .recolector_busqueda  import recolector_busqueda_agent
from .ejecutor_busqueda    import ejecutor_busqueda_agent  


__all__ = [
    "recolector_identidad_agent",
    # "recolector_registro_agent",
    # "recolector_consulta_agent",
    "ejecutor_identidad_agent",   
    # "ejecutor_registro_agent",
    # "ejecutor_consulta_agent",
    "router_agent",
    "bienvenida_agent",
    "agente_politica",
    "validacion_telefono_agent",
    "recolector_busqueda_agent",
    "ejecutor_busqueda_agent",
]
