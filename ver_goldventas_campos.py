# -*- coding: utf-8 -*-
"""Revisa los campos de goldVentas para ver si hay fecha o referencia a la fuente."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldVentas")

filas = list(t.list_entities())
print(f"Total prospectos en goldVentas: {len(filas)}\n")
print("Campos del primer prospecto:")
if filas:
    for k, v in filas[0].items():
        marca = "  <-- FECHA?" if ("fecha" in k.lower() or "date" in k.lower()) else ""
        print(f"  {k:22} = {str(v)[:55]}{marca}")

print("\n--- 3 ejemplos de señal + origen ---")
for e in filas[:3]:
    print(f"  prospecto: {e.get('prospecto','?')}")
    print(f"    senal: {(e.get('senal','') or '')[:60]}")
    print(f"    origen: {e.get('origen','?')}")
    print()