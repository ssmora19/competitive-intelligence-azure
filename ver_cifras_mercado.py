# -*- coding: utf-8 -*-
"""Revisa qué campos tiene goldCifrasMercado (si hay fuente/fecha o solo la cifra)."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldCifrasMercado")

filas = list(t.list_entities())
print(f"Total cifras en goldCifrasMercado: {len(filas)}\n")
for e in filas:
    print("--- Cifra ---")
    for k, v in e.items():
        if not k.startswith("odata"):
            print(f"  {k:20} = {str(v)[:60]}")
    print()