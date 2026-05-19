import os, json, sys

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

sys.path.insert(0, ".")

# KIT 1 — nuevas fuentes
from ingestion_competidores.function_competidores import (
    ingest_noticias_competidores,
    load_companies,
)
companies = load_companies()
print("=== Noticias Competidores ===")
print(ingest_noticias_competidores(companies))

# KIT 2 — nuevas fuentes
from ingestion_clientes.function_clientes import (
    ingest_reportes_sectoriales_clientes,
)
print("\n=== Reportes Sectoriales Clientes ===")
print(ingest_reportes_sectoriales_clientes())

# KIT 4 — nuevas fuentes
from ingestion_proveedores.function_proveedores import (
    ingest_rss_proveedores,
)
print("\n=== RSS Proveedores ===")
print(ingest_rss_proveedores())