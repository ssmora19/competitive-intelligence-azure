# -*- coding: utf-8 -*-
"""
inspeccionar_silver.py
======================

QUÉ HACE
--------
Recorre los contenedores de BLOB Storage 'silver-*' (entrando a sus carpetas por
fuente) y saca una foto real de tus datos de Silver, sin abrir archivos a mano:
  - qué valores de 'clasificacion.tipo_senal' existen en cada contenedor (y cuántos)
  - cuántos registros pasan el umbral de relevancia ('relevancia.pasa_umbral')
  - estado del sentimiento (lleno por reglas / por gemini / null)
  - presencia de campos clave (entidad_declarada, fecha_evento_utc, fecha_cierre…)

PARA QUÉ SIRVE
--------------
Confirmar, sobre tu Azure real, la taxonomía y los nombres de campo exactos, para
reescribir gold_data.py (lectura de blobs) y config_gold.py (taxonomía real) sin
adivinar nada. Es de SOLO LECTURA: no escribe ni modifica nada.

CÓMO SE EJECUTA (desde la carpeta gold/)
    python inspeccionar_silver.py
    python inspeccionar_silver.py 500     # muestrear máx. 500 blobs por contenedor

NOTA
----
Para no descargar miles de blobs, muestrea hasta MAX_POR_CONTENEDOR por contenedor
(por defecto 300). Es suficiente para ver la taxonomía; si quieres exhaustivo, sube
el número como argumento.
"""

from __future__ import annotations

import sys
import json
from collections import Counter

from azure.storage.blob import BlobServiceClient

import gold_data as datos  # reutiliza la carga de la cadena de conexión


CONTENEDORES = [
    "silver-competidores", "silver-clientes", "silver-clientes-competencia",
    "silver-tendencias", "silver-proveedores", "silver-mercado",
    "silver-regulatorio", "silver-capacidades",
]

MAX_POR_CONTENEDOR = 300


def _get(d: dict, *ruta, default=None):
    """QUÉ HACE: navega un dict anidado con tolerancia.
    PARA QUÉ SIRVE: leer campos como clasificacion.tipo_senal sin reventar si falta."""
    actual = d
    for clave in ruta:
        if not isinstance(actual, dict) or clave not in actual:
            return default
        actual = actual[clave]
    return actual


def inspeccionar_contenedor(blob_svc, contenedor: str, tope: int) -> None:
    try:
        cli = blob_svc.get_container_client(contenedor)
        blobs = cli.list_blobs()
    except Exception as ex:  # noqa: BLE001
        print(f"\n### {contenedor}: no se pudo abrir ({ex})")
        return

    tipos = Counter()
    metodos_senal = Counter()
    sent_estado = Counter()
    sent_metodo = Counter()
    relev_pasa = Counter()
    tiene_entidad = 0
    tiene_fecha_cierre = 0
    campos_vistos = set()
    familias = Counter()
    leidos = 0

    for blob in blobs:
        if leidos >= tope:
            break
        try:
            raw = cli.get_blob_client(blob.name).download_blob().readall()
            d = json.loads(raw)
        except Exception:
            continue
        leidos += 1
        campos_vistos.update(d.keys())

        tipos[_get(d, "clasificacion", "tipo_senal", default="(sin tipo)")] += 1
        metodos_senal[_get(d, "clasificacion", "metodo", default="(sin metodo)")] += 1

        s = d.get("sentimiento")
        if s is None:
            sent_estado["null"] += 1
        elif isinstance(s, dict):
            sent_estado["lleno"] += 1
            sent_metodo[s.get("metodo", "(sin metodo)")] += 1
        else:
            sent_estado["otro_formato"] += 1

        relev_pasa[str(_get(d, "relevancia", "pasa_umbral", default="(sin campo)"))] += 1
        if (d.get("entidad_declarada") or d.get("entidad_monitoreada")):
            tiene_entidad += 1
        if d.get("fecha_cierre") or d.get("fecha_cierre_utc") or _get(d, "vigencia", "fecha_fin"):
            tiene_fecha_cierre += 1
        familias[d.get("familia", "(sin familia)")] += 1

    print(f"\n{'='*66}\n### {contenedor}   (muestreados {leidos} blobs)\n{'='*66}")
    if leidos == 0:
        print("   (contenedor vacío o sin JSON legibles)")
        return

    print(f"  tipo_senal (clasificacion.tipo_senal):")
    for t, n in tipos.most_common():
        print(f"      {n:5}  {t}")
    print(f"  metodo de clasificacion: {dict(metodos_senal)}")
    print(f"  relevancia.pasa_umbral:  {dict(relev_pasa)}")
    print(f"  sentimiento (estado):    {dict(sent_estado)}")
    if sent_metodo:
        print(f"  sentimiento (metodo):    {dict(sent_metodo)}")
    print(f"  registros con entidad:   {tiene_entidad}/{leidos}")
    print(f"  registros con fecha_cierre: {tiene_fecha_cierre}/{leidos}")
    print(f"  familias (fuentes):      {dict(familias)}")


def main() -> None:
    tope = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else MAX_POR_CONTENEDOR
    conn = datos._connection_string()
    blob_svc = BlobServiceClient.from_connection_string(conn)

    print(f"\nInspeccionando Silver (blobs), hasta {tope} por contenedor...")
    for c in CONTENEDORES:
        inspeccionar_contenedor(blob_svc, c, tope)

    print(f"\n{'='*66}")
    print("Listo. Cópiame y pégame TODA esta salida para alinear Gold a tus datos reales.")
    print(f"{'='*66}\n")


if __name__ == "__main__":
    main()