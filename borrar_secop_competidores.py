# -*- coding: utf-8 -*-
"""
borrar_secop_competidores.py
============================
Borra TODOS los contratos de SECOP de competidores en bronze-competidores,
para reingestar desde cero con el scraper nuevo (que usa secop_nombres).

Borra SOLO los blobs de la fuente secop (contratos). NO toca:
  - redes sociales, noticias, github, web de competidores
  - ningún otro contenedor (clientes, mercado, etc.)

SEGURIDAD: por defecto SIMULACRO (no borra). Solo borra con --borrar.

USO:  python borrar_secop_competidores.py            # simulacro
      python borrar_secop_competidores.py --borrar   # borra de verdad
"""

from __future__ import annotations
import os, sys, json

CONTENEDOR = "bronze-competidores"


def _conn() -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json"),
              os.path.join("..", "..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING."); sys.exit(1)


def _es_secop(blob_name: str, raw: dict) -> bool:
    """Un blob es de SECOP si su ruta lo indica o si el contenido es un contrato."""
    if blob_name.lower().startswith("secop_competidor/"):
        return True
    if "secop" in blob_name.lower():
        return True
    # respaldo: por contenido (tiene campos de contrato SECOP)
    return "proveedor_adjudicado" in raw or "referencia_del_contrato" in raw


def main() -> None:
    from azure.storage.blob import BlobServiceClient

    borrar = "--borrar" in sys.argv
    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(CONTENEDOR)

    a_borrar = []
    otros = 0
    por_competidor = {}

    print(f"Revisando '{CONTENEDOR}'...\n")
    for blob in cli.list_blobs():
        # descarga mínima solo si hace falta mirar contenido
        es_secop = blob.name.lower().startswith("secop_competidor/") or "secop" in blob.name.lower()
        raw = {}
        if not es_secop:
            try:
                d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
                raw = d.get("raw", {}) or {}
                es_secop = _es_secop(blob.name, raw)
                comp = (d.get("_meta", {}) or {}).get("empresa", "?")
            except Exception:
                comp = "?"
        else:
            comp = "?"
            try:
                d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
                comp = (d.get("_meta", {}) or {}).get("empresa", "?")
            except Exception:
                pass

        if es_secop:
            a_borrar.append(blob.name)
            por_competidor[comp] = por_competidor.get(comp, 0) + 1
        else:
            otros += 1

    print("=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"  Blobs SECOP a borrar:   {len(a_borrar)}")
    print(f"  Otros blobs (se dejan): {otros}")
    print("\nSECOP por competidor:")
    for comp, n in sorted(por_competidor.items(), key=lambda x: -x[1]):
        print(f"  {str(comp)[:30]:30} {n}")

    if not borrar:
        print("\n[SIMULACRO] No borré nada. Corre con --borrar para borrar de verdad.")
        return

    print(f"\nBorrando {len(a_borrar)} blobs de SECOP...")
    n = 0
    for nombre in a_borrar:
        try:
            cli.get_blob_client(nombre).delete_blob()
            n += 1
        except Exception as ex:
            print(f"  error: {nombre}: {ex}")
    print(f"Listo. Borré {n} contratos SECOP de competidores.")
    print("Ahora reingesta con el scraper nuevo.")


if __name__ == "__main__":
    main()
    