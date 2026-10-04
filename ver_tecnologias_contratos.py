# -*- coding: utf-8 -*-
"""Muestra la distribución de categorías y tecnologías en goldContratos."""
import os, sys, json
from collections import Counter

def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"): return v["AZURE_STORAGE_CONNECTION_STRING"]
    sys.exit("No hay conexión")

from azure.data.tables import TableServiceClient
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldContratos")

cat = Counter(); tec = Counter(); tec_tak = Counter()
total = 0
for e in t.list_entities():
    total += 1
    cat[e.get("categoria","?")] += 1
    tec[e.get("tecnologia","?")] += 1
    if e.get("es_tak") == "Sí":
        tec_tak[e.get("tecnologia","?")] += 1

print(f"Total contratos: {total}\n")
print("=== POR CATEGORÍA (UNSPSC) ===")
for c,n in cat.most_common():
    print(f"  {c:24} {n}")
print("\n=== POR TECNOLOGÍA (detectada del objeto) ===")
for c,n in tec.most_common():
    print(f"  {c:24} {n}")
print("\n=== TECNOLOGÍAS DE TAK (sus 14 contratos) ===")
for c,n in tec_tak.most_common():
    print(f"  {c:24} {n}")