# -*- coding: utf-8 -*-
"""
verificar_campos_silver.py
==========================
Revisa contratos SECOP en silver-competidores y confirma que el normalizador
nuevo guardó los campos nuevos: objeto, unspsc, fecha_fin, duracion,
prorrogable, y los financieros.

Solo lee.

USO:  python verificar_campos_silver.py
"""
from __future__ import annotations
import os, sys, json

CONTENEDOR = "silver-competidores"
FUENTE = "secop_competidor"

CAMPOS_NUEVOS = ["objeto", "unspsc", "fecha_firma", "fecha_inicio", "fecha_fin",
                 "duracion", "prorrogable", "valor_pagado", "valor_pendiente_ejecucion",
                 "sector"]


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
    cli = bs.get_container_client(CONTENEDOR)

    revisados = 0
    con_campos = {c: 0 for c in CAMPOS_NUEVOS}
    ejemplos = []

    for b in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        if d.get("fuente_original") != FUENTE:
            continue
        secop = d.get("secop")
        if not isinstance(secop, dict):
            continue
        revisados += 1
        for c in CAMPOS_NUEVOS:
            v = secop.get(c)
            if v is not None and str(v).strip() not in ("", "None", "No definido"):
                con_campos[c] += 1
        if len(ejemplos) < 3 and secop.get("fecha_fin"):
            ejemplos.append(secop)
        if revisados >= 200:
            break

    print("=" * 60)
    print(f"CONTRATOS SECOP revisados en Silver: {revisados}")
    print("=" * 60)
    print("\nCampos nuevos (cuántos contratos los tienen con valor):")
    for c in CAMPOS_NUEVOS:
        n = con_campos[c]
        pct = 100*n/max(revisados,1)
        marca = "OK" if n > 0 else "!! VACÍO"
        print(f"  {c:28} {n:4}/{revisados} ({pct:3.0f}%)  {marca}")

    if ejemplos:
        print("\n" + "=" * 60)
        print("EJEMPLO de un contrato con sus campos nuevos:")
        print("=" * 60)
        e = ejemplos[0]
        for c in CAMPOS_NUEVOS:
            print(f"  {c:28} = {str(e.get(c))[:45]}")
    else:
        print("\n!! Ningún contrato tiene fecha_fin. Revisar el normalizador.")


if __name__ == "__main__":
    main()