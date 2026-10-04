# -*- coding: utf-8 -*-
"""Revisa qué fechas tienen las tendencias en Silver, para filtrar por recencia."""
import os, sys, json
from collections import Counter
from datetime import datetime, timezone

CONT = "silver-tendencias"
FUENTES = {"github_trending", "rss_tech_general", "rss_fabricantes"}

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
cli = bs.get_container_client(CONT)

total = con_fecha = sin_fecha = 0
por_mes = Counter()
campos_fecha = Counter()
ejemplos = []

for b in cli.list_blobs():
    try:
        d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
    except Exception:
        continue
    if d.get("fuente_original") not in FUENTES:
        continue
    total += 1
    # revisar qué campos de fecha tiene
    fev = d.get("fecha_evento_utc")
    fing = d.get("ingest_ts_utc") or d.get("procesado_ts_utc")
    if fev:
        campos_fecha["fecha_evento_utc"] += 1
        con_fecha += 1
        por_mes[str(fev)[:7]] += 1
        if len(ejemplos) < 6:
            ejemplos.append((str(fev)[:10], (d.get("titulo","") or "")[:45]))
    else:
        sin_fecha += 1
    if fing:
        campos_fecha["ingest_ts (scraping)"] += 1

print("=" * 60)
print(f"TENDENCIAS revisadas: {total}")
print("=" * 60)
print(f"  Con fecha_evento_utc: {con_fecha} ({100*con_fecha/max(total,1):.0f}%)")
print(f"  Sin fecha de evento:  {sin_fecha}")
print(f"\n  Campos de fecha disponibles:")
for c, n in campos_fecha.most_common():
    print(f"    {c:28} {n}")
if por_mes:
    print(f"\n  Distribución por mes (fecha_evento):")
    for mes, n in sorted(por_mes.items()):
        print(f"    {mes}: {n}")
print(f"\n  Ejemplos (fecha -> título):")
for f, t in ejemplos:
    print(f"    [{f}] {t}")