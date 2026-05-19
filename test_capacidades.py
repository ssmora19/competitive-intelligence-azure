import os, json, sys

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

sys.path.insert(0, ".")
from ingestion_capacidades.function_capacidades import (
    ingest_web_tak,
    ingest_certificaciones_fabricantes,
    ingest_portales_empleo_ti,
    ingest_benchmarking_capacidades,
)

print("=== Web TAK ===")
print(ingest_web_tak())
