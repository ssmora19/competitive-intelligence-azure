# -*- coding: utf-8 -*-
"""
borrar_secop_todos.py
=====================
Borra los blobs de SECOP VIEJOS de los TRES contenedores Bronze, para
re-scrapear frescos con el secop_client nuevo (que trae UNSPSC, fechas
completas, financiero) y el normalizador ajustado.

Borra SOLO blobs de SECOP. NO toca noticias, redes, github, web.
Identifica SECOP por el prefijo de la fuente en el nombre del blob o por
_meta.fuente que empiece con 'secop'.

SEGURIDAD: simulacro por defecto. Solo borra con --borrar.

USO:  python borrar_secop_todos.py            # simulacro (cuenta)
      python borrar_secop_todos.py --borrar    # borra de verdad
"""
from __future__ import annotations
import os, sys, json
from collections import defaultdict

CONTENEDORES = [
    "bronze-competidores",
    "bronze-clientes",
    "bronze-clientes-competencia",
]


def _conn() -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING."); sys.exit(1)


def _es_secop(nombre_blob: str, doc: dict | None) -> bool:
    # 1) por prefijo del nombre del blob (las fuentes SECOP empiezan con 'secop')
    if nombre_blob.lower().startswith("secop") or "/secop" in nombre_blob.lower():
        return True
    # 2) respaldo: por _meta.fuente
    if doc:
        fuente = (doc.get("_meta", {}) or {}).get("fuente", "")
        if str(fuente).lower().startswith("secop"):
            return True
    return False


def main() -> None:
    from azure.storage.blob import BlobServiceClient
    borrar = "--borrar" in sys.argv
    bs = BlobServiceClient.from_connection_string(_conn())

    total_borrar = 0
    total_otros = 0
    por_contenedor = defaultdict(lambda: [0, 0])   # contenedor -> [secop, otros]
    a_borrar = []   # (contenedor, nombre)

    for cont in CONTENEDORES:
        print(f"Revisando '{cont}'...")
        try:
            cli = bs.get_container_client(cont)
            blobs = list(cli.list_blobs())
        except Exception as ex:
            print(f"  (no se pudo abrir {cont}: {ex})")
            continue
        for b in blobs:
            # primero intentar por nombre (rápido, sin descargar)
            es = _es_secop(b.name, None)
            if not es:
                # descargar para revisar _meta.fuente
                try:
                    d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
                    es = _es_secop(b.name, d)
                except Exception:
                    es = False
            if es:
                total_borrar += 1
                por_contenedor[cont][0] += 1
                a_borrar.append((cont, b.name))
            else:
                total_otros += 1
                por_contenedor[cont][1] += 1

    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    for cont, (secop, otros) in por_contenedor.items():
        print(f"  {cont:30} SECOP a borrar={secop:5}  otros(se dejan)={otros}")
    print(f"\n  TOTAL SECOP a borrar: {total_borrar}")
    print(f"  TOTAL otros (se dejan): {total_otros}")

    if not borrar:
        print("\n[SIMULACRO] No borré nada. Corre con --borrar para borrar de verdad.")
        return

    print(f"\nBorrando {len(a_borrar)} blobs de SECOP...")
    n = 0
    for cont, nombre in a_borrar:
        try:
            bs.get_container_client(cont).get_blob_client(nombre).delete_blob()
            n += 1
        except Exception as ex:
            print(f"  error: {cont}/{nombre}: {ex}")
    print(f"Listo. Borré {n} blobs de SECOP de los 3 contenedores.")
    print("Ahora re-scrapea SECOP con el cliente nuevo.")


if __name__ == "__main__":
    main()