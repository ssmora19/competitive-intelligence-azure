# -*- coding: utf-8 -*-
"""Verifica si las noticias en Silver tienen resumen/descripción para dar contexto."""
import os, sys, json

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

print("Revisando campos de texto/resumen en las noticias...\n")
mostrados = 0
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
        # solo noticias recientes de rss_ecosistema o noticias_economicas
        if d.get("fuente_original") not in ("rss_ecosistema", "noticias_economicas"):
            continue
        titulo = (d.get("titulo","") or "")[:55]
        # buscar campos que puedan tener el resumen
        desc = d.get("descripcion") or d.get("resumen") or d.get("summary") or ""
        contenido = d.get("contenido") or ""
        print(f"TÍTULO: {titulo}")
        print(f"  descripcion: {str(desc)[:90]}")
        if contenido:
            print(f"  contenido: {str(contenido)[:90]}")
        print(f"  campos disponibles: {[k for k in d.keys() if not k.startswith('_')][:12]}")
        print()
        mostrados += 1
        if mostrados >= 4:
            break
    if mostrados >= 4:
        break