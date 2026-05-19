import os, json, sys

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

sys.path.insert(0, ".")
from ingestion_clientes.function_clientes import (
    ingest_secop_licitaciones,
    ingest_secop_contratos_adjudicados,
    ingest_linkedin_jobs,
    ingest_camara_comercio,
    ingest_comunidades_tech,
)

print("=== SECOP Licitaciones abiertas ===")
r1 = ingest_secop_licitaciones()
print(r1)

print("\n=== SECOP Contratos adjudicados ===")
r2 = ingest_secop_contratos_adjudicados()
print(r2)

print("\n=== LinkedIn Jobs ===")
r3 = ingest_linkedin_jobs()
print(r3)

print("\n=== Cámara de Comercio Bogotá ===")
r4 = ingest_camara_comercio()
print(r4)

print("\n=== Comunidades Tech Colombia ===")
r5 = ingest_comunidades_tech()
print(r5)