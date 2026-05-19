"""
shared/storage.py
==================
Escritura de datos crudos en Blob Storage (capa Bronze).

Path en Blob:
  {container}/{fuente}/{empresa}/{fecha}/{row_key}.json

El envelope Bronze contiene:
  _meta → metadatos de ingestión (nunca del contenido)
  raw   → JSON crudo completo sin modificar

Principios:
  - Inmutable: overwrite=False siempre
  - Idempotente: si el blob ya existe, retorna el path sin error
  - Sin interpretación: el raw llega tal como vino de la API
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from azure.storage.blob import BlobServiceClient

log = logging.getLogger("ci.storage")


def _get_container(conn_str: str, container: str):
    return BlobServiceClient.from_connection_string(conn_str).get_container_client(container)


def _normalize_empresa(nombre: str) -> str:
    return nombre.lower().replace(" ", "_").replace("/", "-")[:50]


def make_row_key(fuente: str, empresa: str, identificador: str) -> str:
    """SHA256 determinístico → mismo input = mismo key → idempotencia."""
    raw = f"{fuente}::{empresa}::{identificador}"
    return hashlib.sha256(raw.encode()).hexdigest()


def make_blob_path(fuente: str, empresa: str, row_key: str) -> str:
    """
    Path jerárquico por fuente/empresa/fecha.
    La fecha permite re-procesamiento por día desde Silver.
    """
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{fuente}/{_normalize_empresa(empresa)}/{fecha}/{row_key}.json"


def save_bronze(
    conn_str:    str,
    container:   str,
    fuente:      str,
    empresa:     str,
    item:        dict,
    item_id:     str,
    variable_ic: str = "competidores",
    extra_meta:  Optional[dict] = None,
) -> Optional[str]:
    """
    Guarda un item crudo en Blob Storage Bronze.

    Parámetros:
        conn_str:    connection string de Azure Storage
        container:   nombre del container Blob (ej: "competidores")
        fuente:      "instagram" | "youtube" | "web" | ...
        empresa:     nombre de la empresa (ej: "Accenture")
        item:        dict crudo tal como vino de Apify/YouTube — sin modificar
        item_id:     identificador estable del item (de state_manager.extract_item_id)
        variable_ic: variable de inteligencia competitiva del container
        extra_meta:  metadatos adicionales de ingestión (nunca del contenido)

    Retorna:
        blob_path si guardó exitosamente, None si falló.
        Si el blob ya existe, retorna el path sin error (idempotente).
    """
    row_key   = make_row_key(fuente, empresa, item_id)
    blob_path = make_blob_path(fuente, empresa, row_key)

    envelope = {
        "_meta": {
            "fuente":         fuente,
            "empresa":        empresa,
            "row_key":        row_key,
            "item_id":        item_id,
            "ingest_ts_utc":  datetime.now(timezone.utc).isoformat(),
            "schema_version": "2.0",
            "variable_ic":    variable_ic,
            **(extra_meta or {}),
        },
        "raw": item,
    }

    try:
        container_client = _get_container(conn_str, container)
        blob             = container_client.get_blob_client(blob_path)

        if blob.exists():
            log.debug("Blob ya existe (idempotente): %s", blob_path)
            return blob_path

        blob.upload_blob(
            json.dumps(envelope, ensure_ascii=False, default=str),
            content_type="application/json",
            overwrite=False,
        )
        log.debug("Bronze guardado: %s", blob_path)
        return blob_path

    except Exception as ex:
        log.error("Error guardando blob [%s/%s/%s]: %s", fuente, empresa, blob_path, ex)
        return None


def list_recent_blobs(
    conn_str:  str,
    container: str,
    prefix:    str = "",
    limite:    int = 30,
) -> list[dict]:
    """Lista blobs recientes para observabilidad."""
    try:
        blobs = []
        for b in _get_container(conn_str, container).list_blobs(name_starts_with=prefix):
            blobs.append({
                "nombre":    b.name,
                "bytes":     b.size,
                "modificado": b.last_modified.isoformat() if b.last_modified else "",
            })
            if len(blobs) >= limite:
                break
        return blobs
    except Exception as ex:
        log.error("Error listando blobs: %s", ex)
        return []
