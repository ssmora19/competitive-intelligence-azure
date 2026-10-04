# -*- coding: utf-8 -*-
"""Verifica cuántas noticias en goldMercadoNoticias tienen fecha."""
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

con_fecha = sin_fecha = 0
print("Noticias en goldMercadoNoticias:\n")
for e in sorted(t.list_entities(), key=lambda x: x.get("bloque","")):
    f = e.get("fecha","") or ""
    marca = f if f else "SIN FECHA"
    if f: con_fecha += 1
    else: sin_fecha += 1
    print(f"  [{e.get('bloque','?'):11}] [{marca:12}] {(e.get('titulo','') or '')[:50]}")

print(f"\n  Con fecha: {con_fecha} | Sin fecha: {sin_fecha}")