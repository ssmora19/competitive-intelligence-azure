# -*- coding: utf-8 -*-
"""Revisa qué campos tiene goldMercadoNoticias, incluida la fecha."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldMercadoNoticias")

filas = list(t.list_entities())
print(f"Total noticias en goldMercadoNoticias: {len(filas)}\n")
print("Campos del primer registro:")
if filas:
    for k, v in filas[0].items():
        marca = "  <-- FECHA" if "fecha" in k.lower() or "date" in k.lower() else ""
        print(f"  {k:22} = {str(v)[:50]}{marca}")

# ver si hay campo de fecha con valor
print("\n--- Ejemplos con su fecha (si existe) ---")
for e in filas[:6]:
    titulo = (e.get("titulo","") or "")[:45]
    # buscar cualquier campo de fecha
    fecha = "—"
    for k, v in e.items():
        if ("fecha" in k.lower() or "date" in k.lower()) and v:
            fecha = str(v)[:10]
            break
    bloque = e.get("bloque","?")
    print(f"  [{bloque}] [{fecha}] {titulo}")