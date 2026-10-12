# -*- coding: utf-8 -*-
"""Revisa qué datos de sustento tiene cada prospecto en goldVentas."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldVentas")

filas = list(t.list_entities())
print(f"Total prospectos: {len(filas)}\n")

# campos disponibles
print("Campos de un prospecto:")
if filas:
    for k in filas[0].keys():
        if not k.startswith("odata") and k not in ("PartitionKey","RowKey"):
            print(f"  {k}")

# distribución por origen
print("\nPor origen de la señal:")
orig = Counter(e.get("origen","?") for e in filas)
for o, n in orig.most_common():
    print(f"  {o}: {n}")

# cuántos tienen señal real vs genérica
con_senal_real = sum(1 for e in filas if e.get("origen") in ("noticia","secop"))
print(f"\nCon señal real (noticia/secop): {con_senal_real}")
print(f"Sin señal real (general): {len(filas)-con_senal_real}")

# ejemplos de cada origen
print("\n--- Ejemplos por origen ---")
for origen in ["noticia","secop","general"]:
    ej = [e for e in filas if e.get("origen")==origen][:2]
    for e in ej:
        print(f"[{origen}] {e.get('prospecto','?')[:20]:20} n_secop={e.get('n_secop',0)} fecha={e.get('fecha_senal','')}")
        print(f"    señal: {(e.get('senal','') or '')[:55]}")