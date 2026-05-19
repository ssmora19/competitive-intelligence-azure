"""
function_app.py
===============
Punto de entrada principal de Azure Functions.

Azure detecta las Functions registradas en cada módulo de dominio.
Este archivo importa cada dominio para que sus triggers queden registrados.

Para agregar un nuevo dominio (ej: clientes):
  1. Crear carpeta ingestion_clientes/
  2. Crear ingestion_clientes/function_clientes.py
  3. Agregar el import aquí
"""

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

# Futuros dominios (descomentar cuando se implementen):
# from ingestion_clientes.function_clientes       import app  # noqa: F401
# from ingestion_tendencias.function_tendencias   import app  # noqa: F401
# from ingestion_proveedores.function_proveedores import app  # noqa: F401
# from ingestion_mercado.function_mercado         import app  # noqa: F401
# from ingestion_regulatorio.function_regulatorio import app  # noqa: F401
