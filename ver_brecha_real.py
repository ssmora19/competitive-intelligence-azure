# -*- coding: utf-8 -*-
"""Calcula la brecha real actual: cuántas veces los competidores superan a TAK."""
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

suma_comp = 0.0
suma_tak = 0.0
n_comp = 0
n_tak = 0
for e in t.list_entities():
    try:
        v = float(e.get("valor") or 0)
    except: v = 0
    if e.get("es_tak") == "Sí":
        suma_tak += v; n_tak += 1
    else:
        suma_comp += v; n_comp += 1

print("=" * 55)
print("BRECHA COMPETITIVA REAL (datos actuales)")
print("=" * 55)
print(f"  Competidores: {n_comp} contratos -> ${suma_comp:,.0f}")
print(f"  TAK:          {n_tak} contratos -> ${suma_tak:,.0f}")
print()
if suma_tak > 0:
    veces = suma_comp / suma_tak
    print(f"  Los competidores ganan {veces:.1f} veces más que TAK")
    print(f"  (redondeado: {round(veces)}x)")
    pct_tak = 100 * suma_tak / (suma_comp + suma_tak)
    print(f"  TAK captura el {pct_tak:.1f}% del total")
else:
    print("  TAK no tiene contratos (no se puede calcular la brecha)")