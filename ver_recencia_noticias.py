# -*- coding: utf-8 -*-
"""Cuenta cuántas noticias de mercado hay en cada ventana de tiempo, para decidir el filtro."""
import os, sys, json
from datetime import datetime, timezone, timedelta

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

ahora = datetime.now(timezone.utc)
ventanas = {"1 mes": 30, "2 meses": 60, "3 meses": 90, "6 meses": 180, "12 meses": 365}

for cont in CONTENEDORES:
    try:
        cli = bs.get_container_client(cont)
        blobs = list(cli.list_blobs())
    except Exception:
        continue
    cuenta = {k: 0 for k in ventanas}
    total_con_fecha = 0
    for b in blobs:
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        # solo noticias reales
        if str(d.get("clasificacion",{}).get("tipo_senal","")) == "referencia_web":
            continue
        f = d.get("fecha_evento_utc") or ""
        if not f:
            continue
        try:
            fecha = datetime.fromisoformat(str(f).replace("Z","+00:00"))
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        total_con_fecha += 1
        dias = (ahora - fecha).days
        for k, d_max in ventanas.items():
            if dias <= d_max:
                cuenta[k] += 1

    print(f"\n{cont}  (noticias con fecha: {total_con_fecha})")
    print("  " + "-"*40)
    for k in ventanas:
        print(f"    últimos {k:10} {cuenta[k]:4} noticias")