# -*- coding: utf-8 -*-
"""Revisa si hay datos de LinkedIn/LinkedIn Jobs en Bronze y Silver."""
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

# buscar fuentes de linkedin en todos los contenedores bronze y silver
print("Buscando fuentes de LinkedIn en los contenedores...\n")
for cont in ["bronze-competidores", "silver-competidores"]:
    try:
        cli = bs.get_container_client(cont)
        blobs = list(cli.list_blobs())
    except Exception:
        continue
    fuentes = Counter()
    ejemplos_li = []
    for b in blobs:
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        # bronze usa _meta.fuente, silver usa fuente_original
        fuente = (d.get("_meta",{}) or {}).get("fuente","") or d.get("fuente_original","")
        fuentes[fuente] += 1
        if "linkedin" in fuente.lower() and len(ejemplos_li) < 4:
            titulo = d.get("titulo","") or (d.get("_raw",{}) or {}).get("title","") or ""
            ejemplos_li.append((fuente, str(titulo)[:55]))
    print(f"=== {cont} ===")
    li_fuentes = {f:n for f,n in fuentes.items() if "linkedin" in f.lower()}
    if li_fuentes:
        for f, n in li_fuentes.items():
            print(f"  {f}: {n} registros")
        print("  Ejemplos:")
        for f, t in ejemplos_li:
            print(f"    [{f}] {t}")
    else:
        print("  (sin datos de LinkedIn)")
    print()