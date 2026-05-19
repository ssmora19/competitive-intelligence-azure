import os, json, sys

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

sys.path.insert(0, ".")
from ingestion_tendencias.function_tendencias import (
    ingest_rss_fabricantes,
    ingest_rss_colombia,
    ingest_eventos_tech,
)

print("=== RSS Fabricantes ===")
print(ingest_rss_fabricantes())

