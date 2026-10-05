# -*- coding: utf-8 -*-
"""
function_silver.py
==================

QUÉ HACE
--------
Módulo de Azure Functions (patrón Blueprint) que OPERA la capa Silver: lee los
blobs de un contenedor Bronze, los pasa por el pipeline de transformación y
escribe los registros canónicos en el contenedor Silver correspondiente.

PARA QUÉ SIRVE
--------------
Es la única pieza acoplada a Azure. Toda la inteligencia vive en los módulos
puros (pipeline, calidad, normalizadores, enriquecimiento); aquí solo hay E/S de
blobs, batching y registro de ejecución. Esta separación es deliberada y
defendible: la lógica se prueba offline; la infraestructura se prueba en Azure.

DISEÑO DE EJECUCIÓN
-------------------
- Trigger HTTP con batching por 'desde'/'hasta' (igual que en Bronze), para no
  chocar con el límite de 10 minutos de Azure Functions al procesar contenedores
  grandes.
- Idempotencia: si el registro ya existe en Silver, se salta (skip_existente),
  salvo que se pase 'forzar=1'. Así reprocesar es seguro.
- Un blob defectuoso NO tumba la corrida: se cuenta como error y se sigue.

USO (ejemplos)
--------------
  /api/silver/procesar?contenedor=bronze-competidores&desde=0&hasta=50
  /api/silver/procesar?contenedor=bronze-clientes&fuente=noticias_cliente&desde=0&hasta=50
  /api/silver/procesar?contenedor=bronze-competidores&desde=0&hasta=50&solo_relevantes=1
"""

from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone

import azure.functions as func

from . import config_silver as cfg
from . import pipeline

# Silenciar ruido de los SDK de Azure (mismo criterio que en Bronze).
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

bp = func.Blueprint()

# Conexión a la cuenta de DATOS (stinteligencia), la misma que usan Bronze y Gold.
# No se usa AzureWebJobsStorage: en Azure esa variable es la cuenta interna del
# runtime (arquitecturaic850b), no la de los datos.
_CONN = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")


# ---------------------------------------------------------------------------
# Utilidades de almacenamiento
# ---------------------------------------------------------------------------

def _blob_service():
    """QUÉ HACE: crea el cliente de Blob Storage desde la cadena de conexión. PARA QUÉ SIRVE: acceso único y centralizado al almacenamiento."""
    from azure.storage.blob import BlobServiceClient
    return BlobServiceClient.from_connection_string(_CONN)


def _asegurar_contenedor(bs, nombre: str):
    """QUÉ HACE: devuelve el contenedor, creándolo si no existe. PARA QUÉ SIRVE: los contenedores Silver deben existir antes de escribir."""
    cc = bs.get_container_client(nombre)
    try:
        cc.create_container()
    except Exception:
        pass  # ya existe
    return cc


def _registrar_ejecucion(resumen: dict):
    """
    QUÉ HACE: guarda un log de la corrida en la tabla executionLogsSilver.
    PARA QUÉ SIRVE: trazabilidad de la operación (qué se procesó y con qué
    resultado). Va protegido: si el logging falla, la corrida no se cae.
    """
    try:
        from azure.data.tables import TableServiceClient
        ts = TableServiceClient.from_connection_string(_CONN)
        tabla = ts.create_table_if_not_exists("executionLogsSilver")
        entidad = {
            "PartitionKey": resumen.get("contenedor", "silver"),
            "RowKey": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
            **{k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in resumen.items()},
        }
        tabla.create_entity(entidad)
    except Exception as exc:  # noqa: BLE001
        logging.warning("No se pudo registrar ejecución Silver: %s", exc)


# ---------------------------------------------------------------------------
# FUNCIÓN HTTP PRINCIPAL
# ---------------------------------------------------------------------------

def procesar_tanda(contenedor: str, desde: int = 0, hasta: int = 50,
                   fuente_filtro: str | None = None, forzar: bool = False,
                   solo_relevantes: bool = False,
                   modificados_desde: datetime | None = None) -> dict:
    """
    QUÉ HACE: procesa una tanda de blobs de un contenedor Bronze y escribe los
    registros canónicos en Silver. Devuelve el resumen de la tanda.
    PARA QUÉ SIRVE: es la lógica de la capa, separada del HTTP para que la usen
    tanto el endpoint /silver/procesar como el orquestador del pipeline.
    MODO INCREMENTAL: con 'modificados_desde', solo se procesan los blobs de Bronze
    creados o modificados desde esa fecha (lo nuevo desde la última corrida), en vez
    de releer toda la historia cada día.
    """
    ahora = datetime.now(timezone.utc)
    bs = _blob_service()
    origen = bs.get_container_client(contenedor)

    # --- Listado de blobs de la tanda ---
    try:
        nombres = [b.name for b in origen.list_blobs()
                   if modificados_desde is None or (b.last_modified and b.last_modified >= modificados_desde)]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"no se pudo listar '{contenedor}': {exc}"}
    nombres.sort()
    tanda = nombres[desde:hasta]

    # Contadores de resultado por dimensión de resultado.
    res = {
        "contenedor": contenedor, "fuente_filtro": fuente_filtro,
        "rango": f"{desde}-{hasta}", "total_en_contenedor": len(nombres),
        "en_tanda": len(tanda),
        "modo": "incremental" if modificados_desde else "completo",
        "escritos": 0, "skip_existente": 0, "skip_duplicado": 0,
        "skip_no_relevante": 0, "skip_fuente": 0, "sin_familia": 0, "errores": 0,
        "detalle_errores": [],
    }

    hashes_vistos = set()  # dedupe cross-source dentro de la tanda (UNICIDAD)
    cache_silver = {}      # contenedor Silver -> ContainerClient (se crea una vez)

    for nombre in tanda:
        try:
            crudo = origen.get_blob_client(nombre).download_blob().readall()
            blob = json.loads(crudo)
        except Exception as exc:  # noqa: BLE001
            res["errores"] += 1
            res["detalle_errores"].append({"blob": nombre, "error": f"lectura: {exc}"})
            continue

        meta = blob.get("_meta") or {}

        # Filtro opcional por fuente.
        if fuente_filtro and meta.get("fuente") != fuente_filtro:
            res["skip_fuente"] += 1
            continue

        # --- Transformación (lógica pura) ---
        resultado = pipeline.procesar(blob, ahora=ahora)

        if resultado.estado == "sin_familia":
            res["sin_familia"] += 1
            continue
        if resultado.estado == "error":
            res["errores"] += 1
            res["detalle_errores"].append({"blob": nombre, "error": resultado.motivo})
            continue

        canonico = resultado.canonico

        # Filtro opcional: solo escribir lo que pasa el umbral de relevancia.
        if solo_relevantes and not canonico.get("relevancia", {}).get("pasa_umbral", True):
            res["skip_no_relevante"] += 1
            continue

        # UNICIDAD: dedupe por hash dentro de la tanda.
        h = canonico.get("hash_contenido")
        if h in hashes_vistos:
            res["skip_duplicado"] += 1
            continue
        hashes_vistos.add(h)

        # --- Ruteo a contenedor Silver por variable de IC ---
        destino_nombre = cfg.contenedor_silver_para(meta.get("variable_ic", "desconocido"))
        if destino_nombre not in cache_silver:
            cache_silver[destino_nombre] = _asegurar_contenedor(bs, destino_nombre)
        destino = cache_silver[destino_nombre]

        # Nombre de blob Silver: familia/row_key.json (idempotente y navegable).
        row_key = meta.get("row_key") or h
        blob_destino = f"{resultado.familia}/{row_key}.json"
        cliente_destino = destino.get_blob_client(blob_destino)

        # Idempotencia: no reescribir si ya existe (salvo 'forzar').
        if not forzar:
            try:
                if cliente_destino.exists():
                    res["skip_existente"] += 1
                    continue
            except Exception:
                pass

        # --- Escritura del registro canónico ---
        try:
            cliente_destino.upload_blob(
                json.dumps(canonico, ensure_ascii=False, indent=2).encode("utf-8"),
                overwrite=True,
            )
            res["escritos"] += 1
        except Exception as exc:  # noqa: BLE001
            res["errores"] += 1
            res["detalle_errores"].append({"blob": nombre, "error": f"escritura: {exc}"})

    # Trazabilidad de la corrida.
    _registrar_ejecucion({k: v for k, v in res.items() if k != "detalle_errores"})
    return res


@bp.route(route="silver/procesar", methods=["GET"])
def procesar_silver(req: func.HttpRequest) -> func.HttpResponse:
    """
    QUÉ HACE: procesa una tanda de blobs de un contenedor Bronze (vía procesar_tanda).
    PARA QUÉ SIRVE: punto de entrada manual de la capa, por tandas para cubrir
    contenedores grandes sin timeouts.
    """
    contenedor = req.params.get("contenedor")
    if not contenedor:
        return func.HttpResponse(
            json.dumps({"error": "falta parámetro 'contenedor' (p.ej. bronze-competidores)"}),
            status_code=400, mimetype="application/json",
        )

    res = procesar_tanda(
        contenedor,
        desde=int(req.params.get("desde", 0)),
        hasta=int(req.params.get("hasta", 50)),
        fuente_filtro=req.params.get("fuente"),            # opcional: filtra por _meta.fuente
        forzar=req.params.get("forzar", "0") == "1",        # reprocesar aunque ya exista
        solo_relevantes=req.params.get("solo_relevantes", "0") == "1",
    )
    codigo = 500 if "error" in res else 200
    return func.HttpResponse(
        json.dumps(res, ensure_ascii=False, indent=2),
        status_code=codigo, mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# TIMER (opcional, DESACTIVADO por defecto)
# ---------------------------------------------------------------------------
# Se deja el patrón como referencia, pero comentado: en este proyecto los timers
# se activan a conciencia (lección aprendida en Bronze con Apify). Silver solo lee
# blobs, así que no consume créditos externos; aun así se opera manualmente por
# tandas hasta validar la capa.
#
# @bp.timer_trigger(schedule="0 30 4 * * 0", arg_name="timer", run_on_startup=False)
# def procesar_silver_semanal(timer: func.TimerRequest) -> None:
#     """QUÉ HACE: correría Silver sobre todos los contenedores Bronze. PARA QUÉ SIRVE: automatización futura, una vez validada la capa."""
#     ...