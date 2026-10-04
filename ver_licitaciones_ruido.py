# -*- coding: utf-8 -*-
"""Muestra las licitaciones con su UNSPSC y objeto, para ver por qué pasan el filtro."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldLicitaciones")

print("Licitaciones con su UNSPSC, categoría y objeto:\n")
from collections import Counter
por_cat = {}
for e in t.list_entities():
    cat = e.get("categoria","?")
    por_cat.setdefault(cat, []).append({
        "unspsc": e.get("unspsc",""),
        "objeto": (e.get("objeto","") or "")[:55],
    })

# mostrar ejemplos de cada categoría (especialmente las dudosas)
for cat in ["Otros TI", "Software", "Hardware", "Soporte de software", "Servicios TI"]:
    if cat in por_cat:
        print(f"=== {cat} ({len(por_cat[cat])}) ===")
        for item in por_cat[cat][:6]:
            print(f"  UNSPSC {item['unspsc']:14} | {item['objeto']}")
        print()