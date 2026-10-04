# -*- coding: utf-8 -*-
"""Agrupa los prospectos de goldVentas por el servicio que necesitan."""
import os, sys, json
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldVentas")

# solo prospectos con señal real (noticia o secop), agrupados por servicio
por_servicio = defaultdict(list)
for e in t.list_entities():
    tiene_senal = (e.get("fecha_senal","") or "") or (e.get("n_secop",0) or 0) > 0
    if not tiene_senal:
        continue
    serv = (e.get("servicio","") or "Sin definir").strip()
    por_servicio[serv].append({
        "prospecto": e.get("prospecto","?"),
        "tipo": e.get("tipo","?"),
        "encaje": e.get("nivel_encaje","?"),
    })

print("Prospectos (con señal real) agrupados por SERVICIO que necesitan:\n")
for serv, lst in sorted(por_servicio.items(), key=lambda x: -len(x[1])):
    tipos = f"{sum(1 for p in lst if p['tipo']=='Lead')}L/{sum(1 for p in lst if p['tipo']=='Captura')}C"
    nombres = ", ".join(p["prospecto"][:15] for p in lst[:4])
    print(f"  {serv[:24]:24} {len(lst):2} prospectos ({tipos})  ->  {nombres}")