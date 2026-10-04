# -*- coding: utf-8 -*-
"""Verifica qué fechas y datos tiene la tabla goldRadarTecnologias."""
import os, sys, json

def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"): return v["AZURE_STORAGE_CONNECTION_STRING"]
    sys.exit("No hay conexión")

from azure.data.tables import TableServiceClient
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldRadarTecnologias")

print("=" * 90)
print("RADAR — verificación de fechas y datos completos")
print("=" * 90)
for e in sorted(t.list_entities(), key=lambda x: x.get("orden",0)):
    tec = e.get("tecnologia","?")
    fab = e.get("fabricante","") or "—"
    ents = e.get("entidades_compran","") or "—"
    fsecop = e.get("fecha_reciente","") or "—"
    ftend = e.get("fecha_tendencia_global","") or "—"
    ver = (e.get("veredicto","") or "")[:20]
    print(f"\n  {tec}")
    print(f"    veredicto: {ver}")
    print(f"    fabricante: {fab}")
    print(f"    entidades que compran: {ents}")
    print(f"    fecha contrato reciente (SECOP): {fsecop}")
    print(f"    fecha tendencia global: {ftend}")