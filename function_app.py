"""
function_app.py
===============
Punto de entrada principal de Azure Functions.

Cada dominio (competidores, clientes, mercado, ...) define sus triggers sobre
un Blueprint (app = func.Blueprint()). Aquí se crea la ÚNICA FunctionApp real y
se registran los 7 blueprints con register_functions().
"""

import logging

import azure.functions as func

# ─────────────────────────────────────────────────────────────
# Configuración de logging
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
)

# Silenciar el logging RUIDOSO de las librerías de Azure y HTTP.
# Estas imprimen cada request con todos sus headers (el "diluvio" de líneas).
# Subirlas a WARNING deja solo lo importante, sin perder TUS logs de ingesta.
for _ruidoso in (
    "azure",
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.storage",
    "azure.data.tables",
    "urllib3",
    "requests",
    "charset_normalizer",
):
    logging.getLogger(_ruidoso).setLevel(logging.WARNING)

# ─────────────────────────────────────────────────────────────
# La ÚNICA aplicación real
# ─────────────────────────────────────────────────────────────
app = func.FunctionApp()

# ── Importar el blueprint de cada dominio ────────────────────
from ingestion_competidores.function_competidores import app as bp_competidores  # noqa: E402
from ingestion_clientes.function_clientes         import app as bp_clientes       # noqa: E402
from ingestion_mercado.function_mercado           import app as bp_mercado        # noqa: E402
from ingestion_proveedores.function_proveedores   import app as bp_proveedores    # noqa: E402
from ingestion_capacidades.function_capacidades   import app as bp_capacidades    # noqa: E402
from ingestion_tendencias.function_tendencias     import app as bp_tendencias     # noqa: E402
from ingestion_regulatorio.function_regulatorio   import app as bp_regulatorio    # noqa: E402

# ── Registrar los 7 dominios en la aplicación ────────────────
app.register_functions(bp_competidores)
app.register_functions(bp_clientes)
app.register_functions(bp_mercado)
app.register_functions(bp_proveedores)
app.register_functions(bp_capacidades)
app.register_functions(bp_tendencias)
app.register_functions(bp_regulatorio)