import os, json, sys

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

sys.path.insert(0, ".")
from ingestion_regulatorio.function_regulatorio import (
    ingest_mintic,
    ingest_colombia_compra,
    ingest_normativa_legal,
    ingest_financiamiento_tech,
    ingest_inversion_tech,
    ingest_rss_regulatorio,
)

print("=== MinTIC ===")
print(ingest_mintic())

print("\n=== Colombia Compra Eficiente ===")
print(ingest_colombia_compra())

print("\n=== Normativa Legal ===")
print(ingest_normativa_legal())

print("\n=== Financiamiento Tech ===")
print(ingest_financiamiento_tech())

print("\n=== Inversión Tech ===")
print(ingest_inversion_tech())

print("\n=== RSS Regulatorio ===")
print(ingest_rss_regulatorio())