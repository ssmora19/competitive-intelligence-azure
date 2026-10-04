# -*- coding: utf-8 -*-
"""Revisa si hay PROCESOS SECOP (licitaciones abiertas) scrapeados en Silver."""
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

from azure.storage.blob import BlobServiceClient
bs = BlobServiceClient.from_connection_string(_conn())

# buscar procesos en los contenedores silver
CONTENEDORES = ["silver-competidores", "silver-clientes", "silver-clientes-competencia"]
FUENTES_PROCESOS = ["secop_cliente_procesos", "secop_competencia_procesos", "secop_licitaciones"]

print("Buscando PROCESOS (licitaciones abiertas) en Silver...\n")
total_procesos = 0
abiertos = 0
con_fecha_cierre = 0
ejemplos = []

for cont in CONTENEDORES:
    try:
        cli = bs.get_container_client(cont)
        blobs = list(cli.list_blobs())
    except Exception:
        continue
    for b in blobs:
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        fuente = d.get("fuente_original","")
        if "proceso" not in fuente.lower() and "licitacion" not in fuente.lower():
            continue
        total_procesos += 1
        secop = d.get("secop") or {}
        adjudicado = str(secop.get("adjudicado","")).lower()
        if adjudicado == "no" or "abierto" in str(secop.get("estado","")).lower():
            abiertos += 1
        if secop.get("fecha_cierre") or secop.get("fecha_fin"):
            con_fecha_cierre += 1
        if len(ejemplos) < 5:
            ejemplos.append({
                "objeto": (secop.get("objeto") or d.get("titulo") or "")[:50],
                "adjudicado": secop.get("adjudicado","?"),
                "estado": secop.get("estado","?"),
                "unspsc": secop.get("unspsc","?"),
            })

print(f"Total PROCESOS encontrados: {total_procesos}")
print(f"  Abiertos (adjudicado=No): {abiertos}")
print(f"  Con fecha de cierre: {con_fecha_cierre}")
print("\nEjemplos:")
for e in ejemplos:
    print(f"  [{e['adjudicado']}] {e['objeto']} (unspsc: {e['unspsc']})")