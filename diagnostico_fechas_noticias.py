# -*- coding: utf-8 -*-
"""
diagnostico_fechas_noticias.py
==============================
Revisa las noticias en Silver y reporta cuántas tienen fecha confiable vs
cuántas no (fecha null). Sirve para decidir si podemos filtrar por recencia.
También muestra ejemplos de fechas para ver el rango.
Solo lee.

USO:  python diagnostico_fechas_noticias.py
"""
from __future__ import annotations
import os, sys, json
from collections import Counter

CONTENEDORES = ["silver-competidores", "silver-clientes", "silver-clientes-competencia"]
FUENTES_NOTICIA = {"noticias_competidor", "noticias_cliente", "noticias_competencia"}


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré la conexión."); sys.exit(1)


def main():
    from azure.storage.blob import BlobServiceClient
    bs = BlobServiceClient.from_connection_string(_conn())

    total = con_fecha = sin_fecha = 0
    por_mes = Counter()
    # también revisar ingest_ts (cuándo se scrapeó) como alternativa
    con_ingest = 0
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
            if d.get("fuente_original") not in FUENTES_NOTICIA:
                continue
            total += 1
            fecha = d.get("fecha_evento_utc")
            ingest = d.get("ingest_ts_utc") or d.get("procesado_ts_utc")
            if fecha:
                con_fecha += 1
                por_mes[str(fecha)[:7]] += 1
                if len(ejemplos) < 8:
                    ejemplos.append((str(fecha)[:10], (d.get("titulo") or "")[:55]))
            else:
                sin_fecha += 1
            if ingest:
                con_ingest += 1

    print("=" * 56)
    print("FECHAS EN NOTICIAS")
    print("=" * 56)
    print(f"  Total noticias:            {total}")
    print(f"  Con fecha_evento_utc:      {con_fecha}  ({100*con_fecha/max(total,1):.0f}%)")
    print(f"  SIN fecha (null):          {sin_fecha}  ({100*sin_fecha/max(total,1):.0f}%)")
    print(f"  Con fecha de ingesta:      {con_ingest}  (alternativa para filtrar)")

    if por_mes:
        print("\n  Distribución por mes (de las que tienen fecha):")
        for mes, n in sorted(por_mes.items()):
            print(f"    {mes}: {n}")

    print("\n  Ejemplos (fecha -> título):")
    for f, t in ejemplos:
        print(f"    [{f}] {t}")


if __name__ == "__main__":
    main()