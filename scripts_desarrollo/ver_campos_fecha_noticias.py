# -*- coding: utf-8 -*-
"""Revisa qué campos de fecha tienen las noticias en Silver, por fuente."""
import os, sys, json
from collections import defaultdict

CONTENEDORES = ["silver-mercado", "silver-proveedores"]

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

# por fuente, ver qué campos de fecha tiene y cuáles con valor
por_fuente = defaultdict(lambda: {"total":0, "fecha_evento":0, "otros_campos":set()})

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
        fuente = d.get("fuente_original","?")
        por_fuente[fuente]["total"] += 1
        if d.get("fecha_evento_utc"):
            por_fuente[fuente]["fecha_evento"] += 1
        # buscar todos los campos que parezcan fecha
        for k, v in d.items():
            if ("fecha" in k.lower() or "date" in k.lower() or "publish" in k.lower()) and v:
                por_fuente[fuente]["otros_campos"].add(k)

print("=" * 70)
print("CAMPOS DE FECHA por fuente en Silver")
print("=" * 70)
for f, info in sorted(por_fuente.items()):
    print(f"\n  {f}  (total: {info['total']})")
    print(f"    con fecha_evento_utc: {info['fecha_evento']}/{info['total']}")
    print(f"    campos de fecha vistos: {info['otros_campos'] or 'NINGUNO'}")