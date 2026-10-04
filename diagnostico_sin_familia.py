# -*- coding: utf-8 -*-
"""
diagnostico_sin_familia.py
==========================
QUE HACE: recorre un contenedor Bronze, pasa cada blob por el dispatcher de
Silver, y muestra los que NO reconoce (sin_familia), con las llaves de su 'raw'.
PARA QUE SIRVE: identificar qué son esos blobs no reconocidos para decidir si
hay que agregar una familia, filtrarlos, o si son basura (errores de scraping).

USO:
    python diagnostico_sin_familia.py bronze-competidores
"""
import os
import sys
import json

from azure.storage.blob import BlobServiceClient
from ingestion_silver import dispatcher


def obtener_conexion():
    """Lee AzureWebJobsStorage del entorno o, si no está, de local.settings.json."""
    conn = os.environ.get("AzureWebJobsStorage", "")
    if conn:
        return conn
    # Fallback: leer del local.settings.json del proyecto
    try:
        with open("local.settings.json", encoding="utf-8") as f:
            settings = json.load(f)
        return settings.get("Values", {}).get("AzureWebJobsStorage", "")
    except Exception as e:
        print(f"No se pudo leer local.settings.json: {e}")
        return ""


contenedor = sys.argv[1] if len(sys.argv) > 1 else "bronze-competidores"
CONN = obtener_conexion()

if not CONN:
    print("ERROR: no se encontró la cadena de conexión (AzureWebJobsStorage).")
    print("Verifica que local.settings.json exista en esta carpeta y tenga la clave.")
    sys.exit(1)

bs = BlobServiceClient.from_connection_string(CONN)
cc = bs.get_container_client(contenedor)

print(f"Revisando '{contenedor}' ...\n")
sin_familia = []
total = 0

for blob in cc.list_blobs():
    total += 1
    try:
        data = cc.get_blob_client(blob.name).download_blob().readall()
        obj = json.loads(data)
    except Exception as e:
        sin_familia.append((blob.name, f"NO_SE_PUDO_LEER: {e}", {}))
        continue

    raw = obj.get("raw") or {}
    meta = obj.get("_meta") or {}
    fam = dispatcher.resolver_familia(raw, meta.get("fuente", ""))
    if not fam:
        llaves = list(raw.keys())[:15] if isinstance(raw, dict) else f"raw no es dict: {type(raw)}"
        sin_familia.append((blob.name, meta.get("fuente", "?"), llaves))

print(f"Total blobs revisados: {total}")
print(f"Sin familia encontrados: {len(sin_familia)}\n")
print("="*80)
for i, (nombre, fuente, llaves) in enumerate(sin_familia, 1):
    print(f"\n[{i}] BLOB: {nombre}")
    print(f"    fuente: {fuente}")
    print(f"    llaves del raw: {llaves}")