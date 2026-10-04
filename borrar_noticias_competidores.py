# -*- coding: utf-8 -*-
"""
borrar_noticias_competidores.py
===============================
Borra las noticias VIEJAS de competidores (fuente 'noticias_competidor') de
bronze-competidores, para re-scrapear limpio con los noticias_query afinados.

Borra SOLO las noticias de competidores. NO toca:
  - redes sociales, github, web, SECOP de competidores
  - ningún otro contenedor

SEGURIDAD: por defecto SIMULACRO. Solo borra con --borrar.

USO:  python borrar_noticias_competidores.py            # simulacro
      python borrar_noticias_competidores.py --borrar    # borra de verdad
"""
from __future__ import annotations
import os, sys, json

CONTENEDOR = "bronze-competidores"
FUENTE = "noticias_competidor"


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


def main() -> None:
    from azure.storage.blob import BlobServiceClient
    borrar = "--borrar" in sys.argv
    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(CONTENEDOR)

    a_borrar = []
    por_empresa = {}
    otros = 0

    print(f"Revisando '{CONTENEDOR}'...\n")
    for blob in cli.list_blobs():
        # los blobs de esta fuente empiezan con 'noticias_competidor/'
        es_noticia = blob.name.startswith(FUENTE + "/") or ("/" + FUENTE + "/") in blob.name
        # respaldo: revisar por contenido si el prefijo no coincide
        if not es_noticia and FUENTE not in blob.name:
            otros += 1
            continue
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
            if (d.get("_meta", {}) or {}).get("fuente") != FUENTE:
                otros += 1
                continue
            emp = (d.get("_meta", {}) or {}).get("empresa", "?")
        except Exception:
            emp = "?"
        a_borrar.append(blob.name)
        por_empresa[emp] = por_empresa.get(emp, 0) + 1

    print("=" * 56)
    print("RESUMEN")
    print("=" * 56)
    print(f"  Noticias de competidores a borrar: {len(a_borrar)}")
    print(f"  Otros blobs (se dejan):            {otros}")
    print("\nPor empresa:")
    for e, n in sorted(por_empresa.items(), key=lambda x: -x[1]):
        print(f"  {str(e)[:30]:30} {n}")

    if not borrar:
        print("\n[SIMULACRO] No borré nada. Corre con --borrar para borrar de verdad.")
        return

    print(f"\nBorrando {len(a_borrar)} noticias...")
    n = 0
    for nombre in a_borrar:
        try:
            cli.get_blob_client(nombre).delete_blob()
            n += 1
        except Exception as ex:
            print(f"  error: {nombre}: {ex}")
    print(f"Listo. Borré {n} noticias de competidores.")
    print("Ahora re-scrapea con: /api/competidores/ejecutar?fuente=noticias_competidores")


if __name__ == "__main__":
    main()