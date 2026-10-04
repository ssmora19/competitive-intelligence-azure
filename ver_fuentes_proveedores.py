# -*- coding: utf-8 -*-
"""Muestra los nombres EXACTOS de _meta.fuente en bronze-proveedores."""
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
cli = bs.get_container_client("bronze-proveedores")

fuentes = Counter()
ejemplos = {}
for b in cli.list_blobs():
    try:
        d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
    except Exception:
        continue
    # el nombre de fuente está en _meta.fuente
    meta = d.get("_meta", {}) or {}
    f = meta.get("fuente", "?")
    fuentes[f] += 1
    if f not in ejemplos:
        ejemplos[f] = (d.get("titulo","") or "")[:50]

print("=" * 66)
print("FUENTES en bronze-proveedores (nombre EXACTO en _meta.fuente):")
print("=" * 66)
for f, n in fuentes.most_common():
    print(f"  {f:32} {n:4}  ej: {ejemplos.get(f,'')}")