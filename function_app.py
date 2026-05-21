
import logging

# Configuración global de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
)

# Registrar dominios de ingestión
# Cada import activa los triggers definidos en ese módulo
from ingestion_competidores.function_competidores import app  # noqa: F401
from ingestion_regulatorio.function_regulatorio  import app  # noqa: F401
from ingestion_clientes.function_clientes        import app  # noqa: F401
from ingestion_tendencias.function_tendencias    import app  # noqa: F401
from ingestion_proveedores.function_proveedores  import app  # noqa: F401
from ingestion_mercado.function_mercado          import app  # noqa: F401
from ingestion_capacidades.function_capacidades import app  # noqa: F401

