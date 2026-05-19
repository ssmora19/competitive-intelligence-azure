import os, json, sys

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

sys.path.insert(0, ".")
from ingestion_proveedores.function_proveedores import (
    ingest_web_proveedores_core,
    ingest_marketplaces_tech,
    ingest_licenciamiento,
    ingest_startups_tech_colombia,
    ingest_rss_proveedores,
)

print("=== Web Proveedores Core ===")
print(ingest_web_proveedores_core())

print("\n=== Marketplaces Tech ===")
print(ingest_marketplaces_tech())

print("\n=== Licenciamiento ===")
print(ingest_licenciamiento())

print("\n=== Startups Tech Colombia ===")
print(ingest_startups_tech_colombia())

print("\n=== RSS Proveedores ===")
print(ingest_rss_proveedores())