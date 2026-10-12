# -*- coding: utf-8 -*-
"""Muestra las top 8 capturas por score con su fecha_senal."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldVentas")

caps = [e for e in t.list_entities() if e.get("tipo")=="Captura"]
caps.sort(key=lambda x: float(x.get("score",0) or 0), reverse=True)

print("Top 8 capturas por score (las que muestra la medida):\n")
for e in caps[:8]:
    score = float(e.get("score",0) or 0)
    fecha = e.get("fecha_senal","")
    marca = "SIN FECHA" if not fecha else fecha
    print(f"  score={score:.2f}  {e.get('prospecto','?')[:22]:22}  fecha=[{marca}]")

con = sum(1 for e in caps[:8] if e.get("fecha_senal",""))
print(f"\n  De las 8 que se muestran: {con} tienen fecha, {8-con} no")