"""
ingestion_clientes_competencia/function_clientes_competencia.py
===============================================================
QUÉ HACE:
  Monitorea a los CLIENTES DE LA COMPETENCIA (a quién le vende cada competidor
  de TAK), leyendo el catálogo clientes_competencia.json y vigilando cada
  entidad con las fuentes que apliquen según su 'tipo'.

PARA QUÉ SIRVE:
  Inteligencia competitiva: saber qué entidades trabajan con SETI, IT CROWD,
  Comware, Iteria, etc. Para las PÚBLICAS, sus licitaciones en SECOP son
  oportunidades directas para TAK. Cada entidad queda etiquetada con el
  competidor del que es cliente (trazabilidad para Silver/Gold).

REGLAS POR TIPO:
  publica              -> SECOP (procesos + contratos por entidad) + Google News (+ web)
  privada              -> Google News (+ web)     [las privadas no contratan por SECOP]
  privada_extranjera   -> se ignora (activo=false en el catálogo)

DEPENDE DE (piezas que ya existen en shared/):
  - shared/secop_client.py    -> get_procesos_por_entidad / get_contratos_por_entidad
  - shared/news_client.py     -> buscar_noticias_empresa (Google News)
  - shared/firecrawl_client.py-> scrape_many (web, con filtro statusCode 200)
  - shared/storage.py         -> save_bronze / list_recent_blobs
  - shared/state_manager.py   -> delta (idempotencia por hash)
  - shared/logger.py          -> bitácora de ejecuciones

Escribe en el contenedor:  bronze-clientes-competencia
"""

import os
import sys
import json
import time
import logging

import azure.functions as func

# permite importar shared/ (sube un nivel desde esta carpeta)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.secop_client     import SecopClient
from shared.firecrawl_client import FirecrawlClient
from shared.news_client      import NewsClient
from shared.storage          import save_bronze, list_recent_blobs
from shared.state_manager    import (
    apply_delta, get_cursor, update_cursor, reset_cursor,
    extract_item_id, get_all_cursors, init_delta_table,
)
from shared.logger           import IngestLogger, get_last_runs

# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
CONN_STR         = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
BRONZE_CONTAINER = "bronze-clientes-competencia"
CATALOGO_JSON    = os.path.join(os.path.dirname(__file__), "clientes_competencia.json")
VARIABLE_IC      = "clientes_competencia"

log       = logging.getLogger("ci.clientes_competencia")
app       = func.Blueprint()
secop     = SecopClient()
firecrawl = FirecrawlClient()
news      = NewsClient()


# ─────────────────────────────────────────────────────────────
# CARGA DEL CATÁLOGO
# ─────────────────────────────────────────────────────────────
def get_entidades(solo_activas: bool = True) -> list[dict]:
    """
    QUÉ HACE:  lee clientes_competencia.json y devuelve las entidades.
    PARA QUÉ:  fuente única de a quién monitorear (clientes de la competencia).

    Si solo_activas=True, omite las que tienen "activo": false
    (p.ej. las privadas extranjeras de BMIND, fuera del mercado de TAK).
    """
    try:
        with open(CATALOGO_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        log.error("clientes_competencia.json no encontrado en %s", CATALOGO_JSON)
        return []
    except json.JSONDecodeError as ex:
        log.error("clientes_competencia.json con error de formato: %s", ex)
        return []

    if solo_activas:
        data = [e for e in data if e.get("activo", True)]
    return data


# ─────────────────────────────────────────────────────────────
# INGESTA GENÉRICA (delta + guardado + bitácora)  — idéntica a clientes
# ─────────────────────────────────────────────────────────────
def _ingestar(
    fuente:     str,
    empresa:    str,
    items_raw:  list[dict],
    source_url: str = "",
) -> dict:
    """
    Aplica delta (idempotencia por hash), guarda solo lo nuevo en Bronze y
    registra la corrida en la bitácora. Nunca lanza excepción hacia afuera:
    ante cualquier problema devuelve un dict con el estado.
    """
    logger = IngestLogger(CONN_STR, fuente, empresa).start()

    if not items_raw:
        logger.finish(status="no_data")
        return {"empresa": empresa, "status": "no_data", "items_new": 0}

    cursor = get_cursor(CONN_STR, fuente, empresa)
    delta  = apply_delta(items_raw, cursor)

    log.info("[%s/%s] fetched=%d new=%d skipped=%d",
             fuente, empresa, delta.items_fetched,
             len(delta.items_new), delta.items_skipped)

    if not delta.has_new:
        logger.finish(
            status="skip_delta",
            items_fetched=delta.items_fetched,
            items_skipped=delta.items_skipped,
            batch_hash=delta.batch_hash,
        )
        return {
            "empresa": empresa, "status": "skip_delta",
            "items_fetched": delta.items_fetched, "items_new": 0,
            "items_skipped": delta.items_skipped,
        }

    blob_paths = []
    new_ids    = []
    errores    = 0

    for item in delta.items_new:
        item_id   = extract_item_id(item)
        blob_path = save_bronze(
            conn_str    = CONN_STR,
            container   = BRONZE_CONTAINER,
            fuente      = fuente,
            empresa     = empresa,
            item        = item,
            item_id     = item_id,
            variable_ic = VARIABLE_IC,
        )
        if blob_path:
            blob_paths.append(blob_path)
            new_ids.append(item_id)
        else:
            errores += 1

    if new_ids:
        update_cursor(CONN_STR, fuente, empresa, delta.batch_hash, new_ids, source_url)

    status = "ok" if errores == 0 else "ok_con_errores"
    logger.finish(
        status        = status,
        items_fetched = delta.items_fetched,
        items_new     = len(blob_paths),
        items_skipped = delta.items_skipped + errores,
        batch_hash    = delta.batch_hash,
        blob_paths    = blob_paths,
        error_msg     = f"{errores} items fallaron" if errores else None,
    )

    return {
        "empresa": empresa, "status": status,
        "items_fetched": delta.items_fetched,
        "items_new": len(blob_paths),
        "items_skipped": delta.items_skipped,
        "errores": errores,
    }


# ─────────────────────────────────────────────────────────────
# Utilidad: filtrar 404s del scraping web (dejar solo statusCode 200)
# ─────────────────────────────────────────────────────────────
def _solo_status_ok(items: list[dict]) -> list[dict]:
    """
    Deja pasar solo los items de Firecrawl con status 200. Si un item no trae
    metadata de status, se conserva (para no perder datos buenos).
    """
    limpios = []
    for it in items:
        meta = it.get("metadata") or {}
        sc   = meta.get("statusCode") or meta.get("status_code")
        if sc is None or int(sc) == 200:
            limpios.append(it)
    return limpios


# ─────────────────────────────────────────────────────────────
# MONITOREO DE UNA ENTIDAD (cliente de la competencia)
# ─────────────────────────────────────────────────────────────
def _monitorear_entidad(ent: dict) -> list[dict]:
    """
    QUÉ HACE:  vigila UNA entidad cliente de la competencia según su 'tipo',
               devolviendo un resultado por cada fuente que aplique.
    PARA QUÉ:  aislar cada entidad para que un fallo en una no tumbe a las
               demás (cada llamada externa va en su try/except).

    La etiqueta 'empresa' que se guarda en Bronze incluye el competidor,
    para trazabilidad: "DIAN (cliente de IT CROWD)".
    """
    entidad     = ent.get("entidad", "desconocida")
    competidor  = ent.get("competidor", "?")
    tipo        = ent.get("tipo", "privada")
    nombre_secop= ent.get("nombre_secop")
    web_urls    = ent.get("web_urls") or []
    # etiqueta de trazabilidad: entidad + de qué competidor es cliente
    empresa     = f"{entidad} (cliente de {competidor})"
    resultados  = []

    # ---- 1) SECOP (solo PÚBLICAS, y solo si tienen nombre_secop) --------
    if tipo == "publica" and nombre_secop:
        procesos, contratos = [], []
        try:
            procesos = secop.get_procesos_por_entidad(nombre_secop, limite=30)
        except Exception as ex:
            log.warning("[%s] SECOP procesos falló (%s): %s", entidad, nombre_secop, ex)
        try:
            contratos = secop.get_contratos_por_entidad(nombre_secop, limite=30)
        except Exception as ex:
            log.warning("[%s] SECOP contratos falló (%s): %s", entidad, nombre_secop, ex)

        if procesos:
            resultados.append(_ingestar(
                fuente="secop_competencia_procesos", empresa=empresa,
                items_raw=procesos,
                source_url="https://www.datos.gov.co/resource/p6dx-8zbt.json",
            ))
        if contratos:
            resultados.append(_ingestar(
                fuente="secop_competencia_contratos", empresa=empresa,
                items_raw=contratos,
                source_url="https://www.datos.gov.co/resource/jbjy-vk9h.json",
            ))
    # privadas -> no aplica SECOP (no contratan por ahí)

    # ---- 2) Google News (por nombre) — TODAS las activas ----------------
    query = ent.get("noticias_query") or entidad
    try:
        noticias = news.buscar_noticias_empresa(query, limite=10)
        if noticias:
            resultados.append(_ingestar(
                fuente="noticias_competencia", empresa=empresa,
                items_raw=noticias,
                source_url=f"google_news:{query}",
            ))
    except Exception as ex:
        log.warning("[%s] Google News falló: %s", entidad, ex)

    # ---- 3) Web / sala de prensa (solo si hay URLs verificadas) ---------
    if web_urls:
        try:
            crudo  = firecrawl.scrape_many(web_urls)
            limpio = _solo_status_ok(crudo)
            if limpio:
                resultados.append(_ingestar(
                    fuente="web_competencia", empresa=empresa,
                    items_raw=limpio, source_url=web_urls[0],
                ))
        except Exception as ex:
            log.warning("[%s] Web scraping falló (%s): %s", entidad, web_urls, ex)

    if not resultados:
        resultados.append({"empresa": empresa, "status": "sin_fuentes_activas",
                           "items_new": 0})
    return resultados


# ─────────────────────────────────────────────────────────────
# INGESTA PRINCIPAL (con tandas, por el límite de 10 min de Functions)
# ─────────────────────────────────────────────────────────────
def ingest_clientes_competencia(desde: int = 0, hasta: int | None = None) -> list[dict]:
    """
    QUÉ HACE:  recorre el catálogo de clientes de la competencia y vigila a
               cada entidad activa.
    PARA QUÉ:  Parte del monitoreo de inteligencia sobre la competencia.

    PROCESAMIENTO EN TANDAS (desde/hasta): como cada entidad pública consulta
    SECOP (lento) + noticias, correr todas de una puede exceder el límite de
    10 min de Azure Functions. Se procesa por rangos:
        ?fuente=clientes_competencia&desde=0&hasta=5
        ?fuente=clientes_competencia&desde=5&hasta=10

    Cada entidad va aislada en su try/except: si una falla, las demás siguen.
    """
    entidades = get_entidades(solo_activas=True)
    if not entidades:
        return [{"status": "sin_catalogo", "items_new": 0}]

    total = len(entidades)
    hasta = total if hasta is None else min(hasta, total)
    lote  = entidades[desde:hasta]
    log.info("clientes_competencia: procesando %d..%d de %d", desde, hasta, total)

    todos = []
    for ent in lote:
        nombre = ent.get("entidad", "desconocida")
        try:
            res = _monitorear_entidad(ent)
            todos.extend(res)
        except Exception as ex:
            log.error("[%s] fallo inesperado: %s", nombre, ex)
            todos.append({"empresa": nombre, "status": "error", "detalle": str(ex)})
        time.sleep(0.5)  # cortesía con las APIs
    return todos


# ─────────────────────────────────────────────────────────────
# REGISTRO DE FUENTES
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "clientes_competencia": ingest_clientes_competencia,
}


def _ensure_tables():
    """Crea las tablas de delta/bitácora si no existen (idempotente)."""
    try:
        init_delta_table(CONN_STR)
    except Exception as ex:
        log.warning("init_delta_table: %s", ex)


# ─────────────────────────────────────────────────────────────
# TIMER — corre solo (semanal). NO usa Apify, así que es seguro dejarlo activo.
# ─────────────────────────────────────────────────────────────
@app.timer_trigger(schedule="0 0 14 * * 1", arg_name="timer", run_on_startup=False)
def timer_clientes_competencia(timer: func.TimerRequest) -> None:
    """Lunes 9:00 a.m. (hora Colombia, UTC-5) — monitoreo semanal."""
    _ensure_tables()
    log.info("Clientes competencia (timer): %s",
             ingest_clientes_competencia())


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS — ejecución manual y observabilidad
# ─────────────────────────────────────────────────────────────
@app.function_name(name="clientes_competencia_ejecutar")
@app.route(route="clientes-competencia/ejecutar", methods=["GET", "POST"])
def ejecutar_clientes_competencia(req: func.HttpRequest) -> func.HttpResponse:
    """
    Ejecuta el monitoreo manualmente.
      GET /api/clientes-competencia/ejecutar?fuente=clientes_competencia
      GET /api/clientes-competencia/ejecutar?fuente=clientes_competencia&desde=0&hasta=5
      GET /api/clientes-competencia/ejecutar?entidad=DIAN   (una sola entidad)
    """
    _ensure_tables()

    # Modo: una sola entidad por nombre
    entidad = req.params.get("entidad", "").strip()
    if entidad:
        match = [e for e in get_entidades(solo_activas=False)
                 if e.get("entidad", "").lower() == entidad.lower()]
        if not match:
            return func.HttpResponse(
                json.dumps({"error": f"Entidad '{entidad}' no está en el catálogo",
                            "entidades": [e["entidad"] for e in get_entidades(False)]},
                           ensure_ascii=False),
                status_code=404, mimetype="application/json",
            )
        res = _monitorear_entidad(match[0])
        return func.HttpResponse(
            json.dumps({"entidad": entidad, "resultados": res},
                       ensure_ascii=False, default=str),
            status_code=200, mimetype="application/json",
        )

    fuente = req.params.get("fuente", "").lower()
    if not fuente or (fuente not in FUENTES and fuente != "todas"):
        return func.HttpResponse(
            json.dumps({"error": "Parámetro 'fuente' o 'entidad' requerido.",
                        "opciones": list(FUENTES.keys()) + ["todas"]},
                       ensure_ascii=False),
            status_code=400, mimetype="application/json",
        )

    # parámetros de tanda
    def _int_param(nombre):
        v = req.params.get(nombre)
        return int(v) if v is not None and v.isdigit() else None
    desde = _int_param("desde") or 0
    hasta = _int_param("hasta")

    resultados = []
    targets = FUENTES.items() if fuente == "todas" else [(fuente, FUENTES[fuente])]
    for nombre_f, fn in targets:
        log.info("Manual clientes_competencia: ejecutando %s", nombre_f)
        res = fn(desde=desde, hasta=hasta)
        resultados.extend(res if isinstance(res, list) else [res])
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.function_name(name="clientes_competencia_status")
@app.route(route="clientes-competencia/status", methods=["GET"])
def status_clientes_competencia(req: func.HttpRequest) -> func.HttpResponse:
    """
    Observabilidad.
      GET /api/clientes-competencia/status
      GET /api/clientes-competencia/status?fuente=noticias_competencia
      GET /api/clientes-competencia/status?modo=blobs
      GET /api/clientes-competencia/status?modo=blobs&fuente=secop_competencia_contratos
    """
    fuente = req.params.get("fuente", "")
    modo   = req.params.get("modo", "cursores")
    limite = int(req.params.get("limite", "30"))
    try:
        if modo == "blobs":
            blobs = list_recent_blobs(
                CONN_STR, BRONZE_CONTAINER,
                prefix=f"{fuente}/" if fuente else "", limite=limite,
            )
            return func.HttpResponse(
                json.dumps({"container": BRONZE_CONTAINER, "blobs": blobs},
                           ensure_ascii=False),
                status_code=200, mimetype="application/json",
            )
        if fuente:
            runs = get_last_runs(CONN_STR, fuente, limite)
            return func.HttpResponse(
                json.dumps({"fuente": fuente, "runs": runs},
                           ensure_ascii=False, default=str),
                status_code=200, mimetype="application/json",
            )
        cursors = get_all_cursors(CONN_STR)
        return func.HttpResponse(
            json.dumps({"delta_cursors": cursors}, ensure_ascii=False, default=str),
            status_code=200, mimetype="application/json",
        )
    except Exception as ex:
        return func.HttpResponse(
            json.dumps({"error": str(ex)}, ensure_ascii=False),
            status_code=500, mimetype="application/json",
        )


@app.function_name(name="clientes_competencia_reset")
@app.route(route="clientes-competencia/reset_cursor", methods=["POST"])
def reset_clientes_competencia(req: func.HttpRequest) -> func.HttpResponse:
    """
    Reinicia el cursor de delta de una fuente+empresa (para re-ingerir).
      POST /api/clientes-competencia/reset_cursor?fuente=noticias_competencia&empresa=...
    """
    fuente  = req.params.get("fuente", "")
    empresa = req.params.get("empresa", "")
    if not fuente or not empresa:
        return func.HttpResponse(
            json.dumps({"error": "Se requieren 'fuente' y 'empresa'."},
                       ensure_ascii=False),
            status_code=400, mimetype="application/json",
        )
    try:
        reset_cursor(CONN_STR, fuente, empresa)
        return func.HttpResponse(
            json.dumps({"reset": True, "fuente": fuente, "empresa": empresa},
                       ensure_ascii=False),
            status_code=200, mimetype="application/json",
        )
    except Exception as ex:
        return func.HttpResponse(
            json.dumps({"error": str(ex)}, ensure_ascii=False),
            status_code=500, mimetype="application/json",
        )