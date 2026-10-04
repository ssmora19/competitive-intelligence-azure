# -*- coding: utf-8 -*-
"""
ver_tendencias_silver.py
========================
Revisa qué hay en Silver sobre tendencias tecnológicas (de ThoughtWorks, GitHub,
RSS, etc.) para saber con qué construir la parte global del radar de mercado.
Solo lee.
"""
import os, sys, json
from collections import Counter

# posibles contenedores de tendencias
CONTENEDORES = ["silver-tendencias", "silver-mercado", "silver-proveedores"]

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

for cont in CONTENEDORES:
    try:
        cli = bs.get_container_client(cont)
        blobs = list(cli.list_blobs())
    except Exception:
        print(f"[{cont}] no existe o no accesible\n")
        continue
    fuentes = Counter()
    ejemplos = []
    for b in blobs:
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        f = d.get("fuente_original", "?")
        fuentes[f] += 1
        if len(ejemplos) < 8:
            ejemplos.append((f, (d.get("titulo","") or "")[:60]))
    print("=" * 66)
    print(f"CONTENEDOR: {cont}  ({len(blobs)} blobs)")
    print("=" * 66)
    print("  Fuentes:")
    for f, n in fuentes.most_common():
        print(f"    {f:30} {n}")
    print("  Ejemplos de títulos:")
    for f, t in ejemplos:
        print(f"    [{f[:20]}] {t}")
    print()