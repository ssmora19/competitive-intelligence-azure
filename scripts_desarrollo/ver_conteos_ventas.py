# -*- coding: utf-8 -*-
"""Cuenta los prospectos de goldVentas por tipo y encaje, para verificar las tarjetas."""
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
tipos = Counter(e.get("tipo","?") for e in filas)
encaje = Counter(e.get("nivel_encaje","?") for e in filas)
con_fecha = sum(1 for e in filas if (e.get("fecha_senal","") or ""))

print(f"Total prospectos en goldVentas: {len(filas)}\n")
print("=== POR TIPO (para tarjetas Leads/Capturas) ===")
for tp, n in tipos.most_common():
    print(f"  {tp}: {n}")
print("\n=== POR ENCAJE (para tarjeta Encaje alto) ===")
for en, n in encaje.most_common():
    print(f"  {en}: {n}")
print(f"\n=== Con fecha_senal: {con_fecha}/{len(filas)} ===")
print("\nLas tarjetas deberían mostrar:")
print(f"  Leads calientes:  {tipos.get('Lead',0)}")
print(f"  Por capturar:     {tipos.get('Captura',0)}")
print(f"  Encaje alto:      {encaje.get('Alto',0)}")