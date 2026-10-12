import os
import json
import sys

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

sys.path.insert(0, ".")

from ingestion_competidores.function_competidores import (
    load_companies,
    ingest_youtube,
    ingest_instagram,
    ingest_web,
)

# Probar solo con Accenture para no gastar créditos
companies = [c for c in load_companies() if c["nombre"] == "Accenture"]
print(f"Empresa: {companies[0]['nombre']}\n")

print("=== YouTube ===")
resultado = ingest_youtube(companies)
print(resultado)

print("\n=== Instagram ===")
resultado = ingest_instagram(companies)
print(resultado)

print("\n=== Web ===")
resultado = ingest_web(companies)
print(resultado)