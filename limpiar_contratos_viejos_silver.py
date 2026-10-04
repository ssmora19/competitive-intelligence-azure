# -*- coding: utf-8 -*-
"""
limpiar_contratos_viejos_silver.py
==================================
Borra de silver-competidores (y opcionalmente los otros contenedores) los
contratos SECOP VIEJOS: los que NO tienen los campos nuevos (unspsc / fecha_fin),
o sea los que se procesaron con el normalizador viejo. Deja solo los nuevos
(re-scrapeados con el cliente v3 + normalizador nuevo).

Identifica viejo vs nuevo: un contrato es NUEVO si su bloque secop tiene
'unspsc' con valor. Si no lo tiene -> viejo -> se borra.

Borra SOLO blobs de SECOP. No toca noticias, redes, etc.

SEGURIDAD: simulacro por defecto. Solo borra con --borrar.

USO:  python limpiar_contratos_viejos_silver.py            # simulacro
      python limpiar_contratos_viejos_silver.py --borrar    # borra
"""
from __future__ import annotations
import os, sys, json

CONTENEDOR = "silver-competidores"
FUENTE = "secop_competidor"


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
    borrar = "--borrar" in sys.argv
    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(CONTENEDOR)

    viejos, nuevos, otros = [], 0, 0
    print(f"Revisando '{CONTENEDOR}'...\n")
    for b in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        if d.get("fuente_original") != FUENTE:
            otros += 1
            continue
        secop = d.get("secop") or {}
        # NUEVO si tiene unspsc con valor
        unspsc = str(secop.get("unspsc") or "").strip()
        if unspsc and unspsc.lower() not in ("", "none"):
            nuevos += 1
        else:
            viejos.append(b.name)

    print("=" * 56)
    print("RESUMEN")
    print("=" * 56)
    print(f"  Contratos VIEJOS a borrar (sin unspsc): {len(viejos)}")
    print(f"  Contratos NUEVOS (se conservan):        {nuevos}")
    print(f"  Otros blobs (noticias/redes, se dejan): {otros}")

    if not borrar:
        print("\n[SIMULACRO] No borré nada. Corre con --borrar para borrar de verdad.")
        return

    print(f"\nBorrando {len(viejos)} contratos viejos...")
    n = 0
    for nombre in viejos:
        try:
            cli.get_blob_client(nombre).delete_blob()
            n += 1
        except Exception as ex:
            print(f"  error: {nombre}: {ex}")
    print(f"Listo. Borré {n} contratos viejos. Quedan {nuevos} nuevos con campos completos.")


if __name__ == "__main__":
    main()