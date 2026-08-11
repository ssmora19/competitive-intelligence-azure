"""
shared/state_manager.py

Control de ingestión incremental (DELTA) por fuente/empresa.

Tabla: BronzeDeltaControl
  PartitionKey = fuente   (instagram, youtube, web, ...)
  RowKey       = empresa  (normalizado: accenture, deloitte, ...)

Campos:
  last_hash        → SHA256 del último batch completo
  last_ids_seen    → JSON array con los últimos N ids procesados (rolling window)
  last_ingest_ts   → timestamp de la última ejecución exitosa
  total_ever_seen  → contador histórico acumulado
  source_url       → URL/handle monitorizado

Lógica DELTA (dos niveles):
  1. Hash de batch  → si el batch completo es idéntico al anterior, skip total
  2. Filtro por id  → dentro de un batch parcialmente nuevo, descarta ids ya vistos
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from azure.data.tables import TableServiceClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

log = logging.getLogger("ci.state_manager")

TABLE_DELTA        = "controlIngesta"
MAX_IDS_REMEMBERED = 50  # rolling window — cubre holgadamente los 5-10 items por ejecución


def init_delta_table(conn_str: str) -> None:
    """Crea la tabla de control si no existe. Idempotente."""
    svc = TableServiceClient.from_connection_string(conn_str)
    try:
        svc.create_table(TABLE_DELTA)
        log.info("Tabla creada: %s", TABLE_DELTA)
    except ResourceExistsError:
        pass


def _get_table(conn_str: str):
    return TableServiceClient.from_connection_string(conn_str).get_table_client(TABLE_DELTA)


def _normalize(value: str) -> str:
    return (
        value.lower()
        .replace(" ", "_").replace("/", "-").replace("\\", "-")
        .replace("#", "").replace("?", "").replace("&", "")
        .replace("=", "").replace(".", "_")
    )[:252]


def compute_batch_hash(items: list[dict]) -> str:
    """
    SHA256 del conjunto de ids del batch.
    Usa ids estables (no métricas) para que un cambio en likes
    no se cuente como contenido nuevo.
    """
    _ID_KEYS = ("id", "postId", "videoId", "tweetId", "url", "link")
    fingerprints = []
    for item in items:
        for key in _ID_KEYS:
            val = item.get(key)
            if val:
                fingerprints.append(str(val))
                break
        else:
            fingerprints.append(
                hashlib.md5(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()
            )
    combined = "|".join(sorted(fingerprints))
    return hashlib.sha256(combined.encode()).hexdigest()


def extract_item_id(item: dict) -> str:
    """Identificador más estable posible de un item individual."""
    for key in ("id", "postId", "videoId", "tweetId", "url", "link", "permalink"):
        val = item.get(key)
        if val:
            return str(val)[:200]
    return hashlib.md5(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()

def get_cursor(conn_str: str, fuente: str, empresa: str) -> dict:
    """
    Lee el cursor delta. Si no existe (primera ejecución), retorna vacío.
    """
    try:
        entity = _get_table(conn_str).get_entity(
            partition_key=fuente,
            row_key=_normalize(empresa),
        )
        ids_raw = entity.get("last_ids_seen", "[]")
        return {
            "last_hash":       entity.get("last_hash"),
            "last_ids_seen":   json.loads(ids_raw) if isinstance(ids_raw, str) else [],
            "last_ingest_ts":  entity.get("last_ingest_ts"),
            "total_ever_seen": int(entity.get("total_ever_seen", 0)),
            "source_url":      entity.get("source_url"),
        }
    except ResourceNotFoundError:
        return {
            "last_hash": None, "last_ids_seen": [],
            "last_ingest_ts": None, "total_ever_seen": 0, "source_url": None,
        }


def update_cursor(
    conn_str:   str,
    fuente:     str,
    empresa:    str,
    batch_hash: str,
    new_ids:    list[str],
    source_url: Optional[str] = None,
) -> None:
    """Actualiza el cursor con los ids nuevos confirmados."""
    cursor         = get_cursor(conn_str, fuente, empresa)
    ids_acumulados = (cursor["last_ids_seen"] + new_ids)[-MAX_IDS_REMEMBERED:]

    _get_table(conn_str).upsert_entity({
        "PartitionKey":    fuente,
        "RowKey":          _normalize(empresa),
        "last_hash":       batch_hash,
        "last_ids_seen":   json.dumps(ids_acumulados),
        "last_ingest_ts":  datetime.now(timezone.utc).isoformat(),
        "total_ever_seen": cursor["total_ever_seen"] + len(new_ids),
        "source_url":      source_url or cursor.get("source_url") or "",
    })
    log.debug("Cursor actualizado [%s/%s] nuevos=%d total=%d",
              fuente, empresa, len(new_ids), cursor["total_ever_seen"] + len(new_ids))


def reset_cursor(conn_str: str, fuente: str, empresa: str) -> None:
    """Elimina el cursor para forzar re-ingestión completa."""
    try:
        _get_table(conn_str).delete_entity(
            partition_key=fuente,
            row_key=_normalize(empresa),
        )
        log.info("Cursor reseteado: %s/%s", fuente, empresa)
    except ResourceNotFoundError:
        pass


class DeltaResult:
    def __init__(self, items_fetched, items_new, items_skipped, batch_hash, is_identical_batch):
        self.items_fetched       = items_fetched
        self.items_new           = items_new          # list[dict] — solo los nuevos
        self.items_skipped       = items_skipped
        self.batch_hash          = batch_hash
        self.is_identical_batch  = is_identical_batch

    @property
    def has_new(self) -> bool:
        return len(self.items_new) > 0

    def new_ids(self) -> list[str]:
        return [extract_item_id(i) for i in self.items_new]


def apply_delta(items: list[dict], cursor: dict) -> DeltaResult:
    """
    Aplica el filtro delta en dos niveles:
      1. Hash de batch → skip total si es idéntico al anterior
      2. Filtro por id → descarta ids ya vistos en el rolling window
    """
    batch_hash = compute_batch_hash(items)

    # Nivel 1: batch completo idéntico
    if batch_hash == cursor["last_hash"] and cursor["last_hash"] is not None:
        return DeltaResult(
            items_fetched=len(items), items_new=[],
            items_skipped=len(items), batch_hash=batch_hash,
            is_identical_batch=True,
        )

    # Nivel 2: filtro por id individual
    ids_vistos     = set(cursor["last_ids_seen"])
    items_nuevos   = []
    items_saltados = 0

    for item in items:
        item_id = extract_item_id(item)
        if item_id in ids_vistos:
            items_saltados += 1
        else:
            items_nuevos.append(item)
            ids_vistos.add(item_id)

    return DeltaResult(
        items_fetched=len(items), items_new=items_nuevos,
        items_skipped=items_saltados, batch_hash=batch_hash,
        is_identical_batch=False,
    )


def get_all_cursors(conn_str: str) -> list[dict]:
    """Retorna el estado de todos los cursores para el endpoint /status."""
    return [
        {
            "fuente":          e["PartitionKey"],
            "empresa":         e["RowKey"],
            "last_ingest_ts":  e.get("last_ingest_ts"),
            "total_ever_seen": e.get("total_ever_seen", 0),
            "source_url":      e.get("source_url"),
        }
        for e in _get_table(conn_str).list_entities()
    ]
