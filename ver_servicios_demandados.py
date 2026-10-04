# -*- coding: utf-8 -*-
"""Revisa qué tablas podrían alimentar 'servicios más demandados' y su contenido."""
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
tsc = TableServiceClient.from_connection_string(_conn())

# listar todas las tablas gold
print("Tablas que tienes:")
tablas = [t.name for t in tsc.list_tables()]
for t in sorted(tablas):
    if t.lower().startswith("gold"):
        print(f"  {t}")

# revisar goldComparativoServicios (probable fuente)
print("\n--- goldComparativoServicios (si existe) ---")
try:
    t = tsc.get_table_client("goldComparativoServicios")
    filas = list(t.list_entities())
    print(f"  {len(filas)} filas. Campos: {[k for k in filas[0].keys() if not k.startswith('odata')][:12]}")
    print("  Categorías (por demanda del mercado):")
    for e in sorted(filas, key=lambda x: float(x.get("comp_valor",0) or 0), reverse=True)[:8]:
        print(f"    {e.get('categoria','?')[:28]:28} comp=${float(e.get('comp_valor',0) or 0):,.0f}")
except Exception as ex:
    print(f"  no existe o error: {ex}")

# revisar si hay goldServiciosDemandados
print("\n--- Otras tablas de servicios ---")
for nombre in ["goldServiciosDemandados", "goldDemandaServicios", "goldServicios"]:
    try:
        t = tsc.get_table_client(nombre)
        n = sum(1 for _ in t.list_entities())
        print(f"  {nombre}: {n} filas")
    except Exception:
        pass