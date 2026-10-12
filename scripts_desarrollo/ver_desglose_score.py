# -*- coding: utf-8 -*-
"""Revisa si el desglose de factores del score está guardado en goldVeredictos."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldVeredictos")

# buscar un lead_score o captura para ver sus campos
ejemplos = []
for e in t.list_entities():
    if e.get("tipo_veredicto") in ("lead_score", "oportunidad_captura"):
        ejemplos.append(e)
        if len(ejemplos) >= 2:
            break

print("Campos de un veredicto de venta:\n")
if ejemplos:
    for k, v in ejemplos[0].items():
        val = str(v)[:70]
        marca = "  <-- DESGLOSE?" if ("factor" in k.lower() or "detalle" in k.lower() or "aporte" in k.lower()) else ""
        print(f"  {k:22} = {val}{marca}")
    print("\n--- ¿Hay campo con el desglose de factores? ---")
    for e in ejemplos:
        for k, v in e.items():
            if "factor" in k.lower() or "detalle" in k.lower():
                print(f"  {e.get('entidad_nombre','?')}: {k} = {str(v)[:120]}")
else:
    print("No encontré veredictos de venta.")