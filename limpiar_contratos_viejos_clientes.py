# -*- coding: utf-8 -*-
"""
limpiar_contratos_viejos_clientes.py
====================================
Borra de silver-clientes y silver-clientes-competencia los contratos SECOP
VIEJOS (los que NO tienen 'unspsc', o sea procesados con el normalizador viejo).
Deja solo los nuevos (con campos completos: unspsc, fechas, financiero).

Borra SOLO contratos SECOP. No toca noticias, redes, etc.

SEGURIDAD: simulacro por defecto. Solo borra con --borrar.

USO:  python limpiar_contratos_viejos_clientes.py            # simulacro
      python limpiar_contratos_viejos_clientes.py --borrar    # borra
"""
from __future__ import annotations
import os, sys, json

CONTENEDORES = ["silver-clientes", "silver-clientes-competencia"]
# fuentes SECOP en cada contenedor
FUENTES_SECOP = {
    "secop_cliente_contratos", "secop_cliente_procesos", "secop_cliente_adjudicatario",
    "secop_competencia_contratos", "secop_competencia_procesos",
}


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

    total_viejos, total_nuevos, total_otros = 0, 0, 0
    a_borrar = []

    for cont in CONTENEDORES:
        try:
            cli = bs.get_container_client(cont)
            blobs = list(cli.list_blobs())
        except Exception as ex:
            print(f"  (no se pudo abrir {cont}: {ex})")
            continue
        viejos = nuevos = otros = 0
        for b in blobs:
            try:
                d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
            except Exception:
                continue
            # ¿es un contrato SECOP?
            if d.get("fuente_original") not in FUENTES_SECOP:
                otros += 1
                continue
            secop = d.get("secop") or {}
            unspsc = str(secop.get("unspsc") or "").strip()
            if unspsc and unspsc.lower() not in ("", "none"):
                nuevos += 1
            else:
                viejos += 1
                a_borrar.append((cont, b.name))
        print(f"  {cont:30} viejos={viejos:5}  nuevos={nuevos:5}  otros(no SECOP)={otros}")
        total_viejos += viejos
        total_nuevos += nuevos
        total_otros += otros

    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"  Contratos VIEJOS a borrar (sin unspsc): {total_viejos}")
    print(f"  Contratos NUEVOS (se conservan):        {total_nuevos}")
    print(f"  Otros blobs (noticias/redes, se dejan): {total_otros}")

    if not borrar:
        print("\n[SIMULACRO] No borré nada. Corre con --borrar para borrar de verdad.")
        return

    print(f"\nBorrando {len(a_borrar)} contratos viejos...")
    n = 0
    for cont, nombre in a_borrar:
        try:
            bs.get_container_client(cont).get_blob_client(nombre).delete_blob()
            n += 1
        except Exception as ex:
            print(f"  error: {cont}/{nombre}: {ex}")
    print(f"Listo. Borré {n} contratos viejos. Quedan {total_nuevos} nuevos completos.")


if __name__ == "__main__":
    main()