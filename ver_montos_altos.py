# -*- coding: utf-8 -*-
"""Muestra los contratos más CAROS de goldContratos, para detectar montos inflados."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldContratos")

contratos = []
for e in t.list_entities():
    try:
        v = float(e.get("valor") or 0)
    except: v = 0
    contratos.append((v, e.get("categoria","?"), e.get("competidor","?"),
                      (e.get("objeto","") or "")[:55], e.get("es_tak","?")))

contratos.sort(reverse=True)
print("=" * 80)
print("TOP 15 CONTRATOS MÁS CAROS (para detectar montos inflados)")
print("=" * 80)
for v, cat, comp, obj, tak in contratos[:15]:
    print(f"  ${v:>18,.0f}  [{cat[:22]:22}] {comp[:15]:15}")
    print(f"                       {obj}")

# estadística
valores = [c[0] for c in contratos]
print("\n" + "=" * 80)
print(f"  Total contratos: {len(valores)}")
print(f"  Monto máximo: ${max(valores):,.0f}")
print(f"  Suma total: ${sum(valores):,.0f}")
# cuántos superan mil millones
mil_M = sum(1 for v in valores if v >= 1_000_000_000)
diez_mil_M = sum(1 for v in valores if v >= 10_000_000_000)
print(f"  Contratos >= $1.000 millones: {mil_M}")
print(f"  Contratos >= $10.000 millones: {diez_mil_M}  <- si hay, revisar si son reales")