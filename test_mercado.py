import os, json, sys

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

sys.path.insert(0, ".")
from ingestion_mercado.function_mercado import (
    ingest_dane_estadisticas,
    ingest_reportes_economicos,
    ingest_estudios_mercado_ti,
    ingest_noticias_economicas,
)

print("=== DANE Estadísticas ===")
print(ingest_dane_estadisticas())
