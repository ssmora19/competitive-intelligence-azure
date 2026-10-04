# -*- coding: utf-8 -*-
"""
inspeccionar_fechas.py
======================
Busca en los blobs de Silver los registros con fecha_evento sospechosa (futuras,
o muy viejas) y muestra su título, fuente y la fecha cruda. Sirve para entender de
dónde salen las fechas raras (2029, 2020…) antes de decidir cómo filtrarlas.

De solo lectura. Ejecuta (desde gold/):  python inspeccionar_fechas.py
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from azure.storage.blob import BlobServiceClient
import gold_data as datos

CONTENEDORES = [
    "silver-competidores", "silver-clientes", "silver-clientes-competencia",
    "silver-tendencias", "silver-proveedores", "silver-mercado",
    "silver-regulatorio", "silver-capacidades",
]

ANIO_HOY = datetime.now(timezone.utc).year


def _parse(v):
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    for intento in (s, s[:10]):
        try:
            return datetime.fromisoformat(intento)
        except (ValueError, TypeError):
            continue
    return None


def main():
    svc = BlobServiceClient.from_connection_string(datos._connection_string())
    print(f"\nHoy es {ANIO_HOY}. Buscando fechas sospechosas (futuras o < 2024)...\n")

    futuras, viejas = [], []
    for contenedor in CONTENEDORES:
        try:
            cli = svc.get_container_client(contenedor)
        except Exception:
            continue
        for blob in cli.list_blobs():
            try:
                d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
            except Exception:
                continue
            # solo relevantes con evento (los que llegan a Gold)
            relev = d.get("relevancia", {}) or {}
            if relev.get("pasa_umbral") is not True:
                continue
            fecha = _parse(d.get("fecha_evento_utc"))
            if fecha is None:
                continue
            info = {
                "contenedor": contenedor.replace("silver-", ""),
                "fecha_evento_utc": d.get("fecha_evento_utc"),
                "ingest_ts": d.get("ingest_ts_utc", ""),
                "familia": d.get("familia", ""),
                "titulo": (d.get("titulo", "") or "")[:70],
            }
            if fecha.year > ANIO_HOY:
                futuras.append(info)
            elif fecha.year < 2024:
                viejas.append(info)

    print("=" * 70)
    print(f"FECHAS FUTURAS (año > {ANIO_HOY}) — {len(futuras)} registros")
    print("=" * 70)
    for r in futuras[:30]:
        print(f"\n  [{r['contenedor']}] fecha_evento={r['fecha_evento_utc']}")
        print(f"     ingesta={r['ingest_ts']}  familia={r['familia']}")
        print(f"     título: {r['titulo']}")

    print("\n" + "=" * 70)
    print(f"FECHAS VIEJAS (año < 2024) — {len(viejas)} registros")
    print("=" * 70)
    for r in viejas[:30]:
        print(f"\n  [{r['contenedor']}] fecha_evento={r['fecha_evento_utc']}")
        print(f"     ingesta={r['ingest_ts']}  familia={r['familia']}")
        print(f"     título: {r['titulo']}")

    print(f"\n\nTotal: {len(futuras)} futuras, {len(viejas)} viejas.")
    print("Con esto vemos si son errores de parseo o noticias legítimas con fecha rara.\n")


if __name__ == "__main__":
    main()