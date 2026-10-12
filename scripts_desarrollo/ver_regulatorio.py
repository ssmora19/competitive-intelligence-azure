# -*- coding: utf-8 -*-
"""Revisa qué hay en silver-regulatorio (para las alertas regulatorias)."""
import os, sys, json

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
cli = bs.get_container_client("silver-regulatorio")

from collections import Counter
fuentes = Counter()
ejemplos = []
con_fecha = 0
for b in cli.list_blobs():
    try:
        d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
    except Exception:
        continue
    fuentes[d.get("fuente_original","?")] += 1
    if d.get("fecha_evento_utc"):
        con_fecha += 1
    if len(ejemplos) < 8:
        ejemplos.append({
            "titulo": (d.get("titulo","") or "")[:60],
            "fecha": str(d.get("fecha_evento_utc",""))[:10],
            "fuente": d.get("fuente_original","?"),
        })

print(f"Total regulatorio: {sum(fuentes.values())} | con fecha: {con_fecha}\n")
print("Fuentes:")
for f, n in fuentes.most_common():
    print(f"  {f}: {n}")
print("\nEjemplos:")
for e in ejemplos:
    print(f"  [{e['fecha'] or 'sin fecha'}] {e['titulo']}")