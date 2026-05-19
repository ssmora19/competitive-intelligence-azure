"""
shared/logger.py
================
Logs operativos de ingestión en Azure Table Storage.

Tabla: BronzeIngestLog
  PartitionKey = fuente  (instagram, youtube, web, ...)
  RowKey       = empresa__YYYYMMDDTHHMMSS

Un registro por ejecución por empresa/fuente:
  - cuántos items se obtuvieron
  - cuántos eran realmente nuevos
  - cuántos se saltaron por delta
  - si hubo error y cuál
  - cuánto tardó
"""

import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional

from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceExistsError

log = logging.getLogger("ci.logger")

TABLE_LOG = "executionLogs"


def _get_table(conn_str: str):
    svc = TableServiceClient.from_connection_string(conn_str)
    try:
        svc.create_table(TABLE_LOG)
    except ResourceExistsError:
        pass
    return svc.get_table_client(TABLE_LOG)


def _normalize(value: str) -> str:
    return (
        value.lower()
        .replace(" ", "_").replace("/", "-").replace("\\", "-")
        .replace("#", "").replace("?", "").replace("&", "")
        .replace("=", "").replace(".", "_")
    )[:252]


class IngestLogger:
    """
    Registra el resultado de una ejecución de ingestión.

    Uso:
        logger = IngestLogger(conn_str, fuente="instagram", empresa="Accenture")
        logger.start()
        # ... procesar ...
        logger.finish(status="ok", items_fetched=10, items_new=3, ...)
    """

    def __init__(self, conn_str: str, fuente: str, empresa: str):
        self._conn_str = conn_str
        self._fuente   = fuente
        self._empresa  = empresa
        self._start    = time.monotonic()
        self._ts       = datetime.now(timezone.utc)

    def start(self) -> "IngestLogger":
        self._start = time.monotonic()
        self._ts    = datetime.now(timezone.utc)
        return self

    def finish(
        self,
        status:        str,
        items_fetched: int       = 0,
        items_new:     int       = 0,
        items_skipped: int       = 0,
        batch_hash:    str       = "",
        blob_paths:    list[str] = None,
        error_msg:     Optional[str] = None,
    ) -> None:
        duracion = round(time.monotonic() - self._start, 2)
        ts_str   = self._ts.strftime("%Y%m%dT%H%M%S")
        row_key  = f"{_normalize(self._empresa)}__{ts_str}"

        entity = {
            "PartitionKey":  self._fuente,
            "RowKey":        row_key,
            "empresa":       self._empresa,
            "status":        status,
            "items_fetched": items_fetched,
            "items_new":     items_new,
            "items_skipped": items_skipped,
            "duracion_seg":  duracion,
            "batch_hash":    batch_hash or "",
            "blob_paths":    json.dumps((blob_paths or [])[:20]),  # máx 20 para no exceder 64KB
            "error_msg":     error_msg or "",
            "ingest_ts_utc": self._ts.isoformat(),
        }

        try:
            _get_table(self._conn_str).create_entity(entity)
            log.info(
                "[%s/%s] status=%s nuevos=%d/%d dur=%.1fs",
                self._fuente, self._empresa,
                status, items_new, items_fetched, duracion,
            )
        except Exception as ex:
            log.error("Error guardando log: %s", ex)

    def error(self, msg: str, items_fetched: int = 0) -> None:
        self.finish(status="error", items_fetched=items_fetched, error_msg=str(msg)[:1000])


def get_last_runs(conn_str: str, fuente: str, limite: int = 30) -> list[dict]:
    """Retorna las últimas N ejecuciones de una fuente para observabilidad."""
    tabla    = _get_table(conn_str)
    entities = list(tabla.query_entities(
        f"PartitionKey eq '{fuente}'", results_per_page=limite
    ))
    return [
        {
            "empresa":       e.get("empresa"),
            "status":        e.get("status"),
            "items_fetched": e.get("items_fetched", 0),
            "items_new":     e.get("items_new", 0),
            "items_skipped": e.get("items_skipped", 0),
            "duracion_seg":  e.get("duracion_seg", 0),
            "ingest_ts_utc": e.get("ingest_ts_utc"),
            "error_msg":     e.get("error_msg") or None,
        }
        for e in entities
    ]
