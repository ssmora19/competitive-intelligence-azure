# -*- coding: utf-8 -*-
"""Muestra los UNSPSC a 6 dígitos y ejemplos, para subdividir mejor las categorías."""
import os, sys, json, re
from collections import defaultdict

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

# agrupar por código a 6 dígitos
por_codigo = defaultdict(lambda: [0, ""])
for e in t.list_entities():
    unspsc = str(e.get("unspsc") or "")
    # V1.81111808 -> 811118 (6 dígitos)
    m = re.search(r"(\d{6})", unspsc)
    if m:
        cod = m.group(1)
        por_codigo[cod][0] += 1
        if not por_codigo[cod][1]:
            por_codigo[cod][1] = (e.get("objeto","") or "")[:50]

print("=" * 70)
print("CÓDIGOS UNSPSC a 6 dígitos (para subdividir categorías)")
print("=" * 70)
print(f"  {'código':8} {'conteo':>7}  ejemplo de objeto")
print("  " + "-" * 66)
for cod, (n, ej) in sorted(por_codigo.items(), key=lambda x:-x[1][0]):
    if n >= 2:  # solo los que tienen 2+ contratos
        print(f"  {cod:8} {n:>7}  {ej}")