"""
ingestion_clientes/function_clientes.py
=======================================
Azure Function — KIT 2: Comportamiento y necesidades de clientes

Fuentes Bronze:
  1. SECOP II — licitaciones abiertas TI (clientes potenciales sector público)
  2. SECOP II — contratos TI adjudicados (quién está comprando tech)
  3. Apify LinkedIn Jobs — qué perfiles TI buscan las empresas en Colombia
  4. Firecrawl — Cámara de Comercio de Bogotá (directorio empresarial)
  5. Firecrawl — comunidades y foros tech Colombia (necesidades del mercado)

Container Bronze: bronze-clientes

Triggers:
  Diario   → SECOP licitaciones (alta volatilidad — una licitación puede
              abrirse y cerrarse en días)
  Semanal  → SECOP contratos adjudicados + LinkedIn Jobs
  Mensual  → Cámara de Comercio + foros tech

Por qué SECOP está aquí y no en regulatorio:
  SECOP detecta CLIENTES POTENCIALES — entidades públicas comprando TI.
  TAK participa en el 3-5% de oportunidades disponibles.
  Este módulo busca el 95-97% que están perdiendo.
"""

import os
import sys
import json
import time
import logging
from typing import Optional

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.secop_client     import SecopClient
from shared.apify_client     import ApifyClient
from shared.firecrawl_client import FirecrawlClient
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
BRONZE_CONTAINER = "bronze-clientes"

log       = logging.getLogger("ci.clientes")
app       = func.FunctionApp()
secop     = SecopClient()
apify     = ApifyClient()
firecrawl = FirecrawlClient()


# ─────────────────────────────────────────────────────────────
# ORQUESTADOR DELTA — igual que en los demás módulos
# ─────────────────────────────────────────────────────────────

def _ingestar(
    fuente:     str,
    empresa:    str,
    items_raw:  list[dict],
    source_url: str = "",
) -> dict:
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
            variable_ic = "clientes",
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
# INGESTORES
# ─────────────────────────────────────────────────────────────

def ingest_secop_licitaciones() -> dict:
    """
    Licitaciones TI abiertas ahora en SECOP II.

    Estas son oportunidades ACTIVAS que TAK podría estar tomando.
    TAK solo participa en el 3-5% — aquí está el 95% que está perdiendo.

    Frecuencia: diaria — una licitación puede cerrarse en días.
    """
    items_raw = secop.get_procesos_activos_tak(limite=200)
    return _ingestar(
        fuente     = "secop_licitaciones",
        empresa    = "sector_publico",
        items_raw  = items_raw,
        source_url = "https://www.datos.gov.co/resource/p6dx-8zbt.json",
    )


def ingest_secop_contratos_adjudicados() -> dict:
    """
    Contratos TI adjudicados en los últimos 90 días.

    Detecta qué entidades públicas están comprando tecnología
    relevante para TAK (Oracle, nube, infraestructura, etc.)
    Estas entidades son clientes potenciales — ya compraron tech similar.

    Frecuencia: semanal — los contratos adjudicados no cambian tan rápido.
    """
    items_raw = secop.get_contratos_tak(dias_recientes=90, limite=200)
    return _ingestar(
        fuente     = "secop_contratos",
        empresa    = "sector_publico",
        items_raw  = items_raw,
        source_url = "https://www.datos.gov.co/resource/jbjy-vk9h.json",
    )


def ingest_linkedin_jobs() -> dict:
    """
    Ofertas de empleo TI en Colombia vía LinkedIn Jobs Scraper.
    Actor: valig~linkedin-jobs-scraper — gratuito, sin cookies.

    Detecta qué tecnologías demandan las empresas colombianas.
    Si una empresa busca Oracle DBA o Snowflake Engineer → cliente potencial.
    """
    keywords = [
        "Oracle DBA",
        "Snowflake",
        "Oracle Cloud",
        "implementacion ERP",
        "migracion nube",
        "infraestructura TI",
        "ciberseguridad",
        "base de datos",
    ]

    todos_items = []
    for keyword in keywords:
        items = apify.run_actor(
            "valig~linkedin-jobs-scraper",
            {
                "keywords": keyword,
                "location": "Colombia",
                "limit":    10,
            }
        )
        for item in items:
            item["_keyword_buscada"] = keyword
        todos_items.extend(items)
        apify.throttle()

    return _ingestar(
        fuente     = "linkedin_jobs",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "https://www.linkedin.com/jobs/",
    )


def ingest_camara_comercio() -> dict:
    """
    Directorio empresarial Cámara de Comercio de Bogotá — vía Firecrawl.
    Detecta empresas del sector TI en Bogotá.
    Frecuencia: mensual.
    """
    urls = [
        "https://www.ccb.org.co/Transformacion-empresarial/Innovacion-y-Tecnologia",
        "https://www.ccb.org.co/Clusters/Cluster-de-Bogota-Region-en-Software-y-TI",
        "https://www.ccb.org.co/en-bogota-y-la-region/Sectores-estrategicos/Tecnologia",
    ]
    items_raw = firecrawl.scrape_many(urls)
    return _ingestar(
        fuente     = "camara_comercio_bogota",
        empresa    = "camara_comercio",
        items_raw  = items_raw,
        source_url = "https://www.ccb.org.co",
    )


def ingest_comunidades_tech() -> dict:
    """
    Comunidades y eventos tech Colombia — necesidades del mercado.
    Detecta tendencias y pain points del sector TI colombiano.
    Frecuencia: mensual.
    """
    urls = [
        "https://www.meetup.com/es/cities/co/bogota/tech/",
        "https://platzi.com/blog/",
        "https://medium.com/tag/tecnologia-colombia",
    ]
    items_raw = firecrawl.scrape_many(urls)
    return _ingestar(
        fuente     = "comunidades_tech",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "comunidades_tech_colombia",
    )


def ingest_redes_sociales_clientes() -> dict:
    """
    X/Twitter — conversaciones sobre necesidades TI de clientes.

    Detecta empresas colombianas buscando soluciones tech,
    quejas sobre sistemas actuales, migraciones planeadas.

    Frecuencia: mensual.
    """
    hashtags = [
        "#OracleERP Colombia",
        "#migracion nube Colombia empresa",
        "#transformacion digital empresa Colombia",
        "#licitacion tecnologia Colombia",
        "#software empresarial Colombia",
    ]

    todos_items = []
    for hashtag in hashtags:
        items = apify.run_actor(ACTOR_X, {
            "searchTerms":     [hashtag],
            "maxTweets":       10,
            "sort":            "Latest",
            "withReplies":     False,
            "includeUserInfo": True,
        })
        for item in items:
            item["_hashtag_buscado"] = hashtag
        todos_items.extend(items)
        apify.throttle()

    return _ingestar(
        fuente     = "redes_sociales_clientes",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "twitter_clientes_ti_colombia",
    )


def ingest_reportes_sectoriales_clientes() -> dict:
    """
    Reportes sectoriales de clientes potenciales de TAK.

    Sector público y privado colombiano — quién está invirtiendo en tech,
    qué sectores están en transformación digital.

    Frecuencia: mensual.
    """
    urls = [
        "https://www.asobancaria.com/noticias/",
        "https://www.andi.com.co/Home/Noticia",
        "https://www.mintic.gov.co/portal/inicio/Noticias/",
        "https://www.minhacienda.gov.co/webcenter/portal/MinHacienda/pages_home/Sala-de-prensa/Noticias",
        "https://www.dnp.gov.co/Noticias/Paginas/Noticias.aspx",
    ]
    items_raw = firecrawl.scrape_many(urls)
    return _ingestar(
        fuente     = "reportes_sectoriales_clientes",
        empresa    = "sectores_colombia",
        items_raw  = items_raw,
        source_url = "asobancaria_andi_mintic_minhacienda",
    )


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "secop_licitaciones":           ingest_secop_licitaciones,
    "secop_contratos":              ingest_secop_contratos_adjudicados,
    "linkedin_jobs":                ingest_linkedin_jobs,
    "camara_comercio":              ingest_camara_comercio,
    "comunidades_tech":             ingest_comunidades_tech,
    "redes_sociales_clientes":      ingest_redes_sociales_clientes,
    "reportes_sectoriales_clientes": ingest_reportes_sectoriales_clientes,
}


def _ensure_tables():
    try:
        init_delta_table(CONN_STR)
    except Exception as ex:
        log.warning("Tablas de control: %s", ex)


# ─────────────────────────────────────────────────────────────
# TRIGGERS — Timer
# ─────────────────────────────────────────────────────────────

@app.timer_trigger(schedule="0 0 12 * * *", arg_name="timer", run_on_startup=False)
def timer_secop_licitaciones(timer: func.TimerRequest) -> None:
    """SECOP licitaciones — diario 7:00am Colombia (12:00 UTC)."""
    _ensure_tables()
    resultado = ingest_secop_licitaciones()
    log.info("Clientes SECOP licitaciones: %s", resultado)


@app.timer_trigger(schedule="0 0 13 * * 1", arg_name="timer", run_on_startup=False)
def timer_secop_contratos(timer: func.TimerRequest) -> None:
    """SECOP contratos adjudicados — lunes 8:00am Colombia (13:00 UTC)."""
    _ensure_tables()
    resultado = ingest_secop_contratos_adjudicados()
    log.info("Clientes SECOP contratos: %s", resultado)


@app.timer_trigger(schedule="0 30 13 * * 1", arg_name="timer", run_on_startup=False)
def timer_linkedin_jobs(timer: func.TimerRequest) -> None:
    """LinkedIn Jobs — lunes 8:30am Colombia (13:30 UTC)."""
    _ensure_tables()
    resultado = ingest_linkedin_jobs()
    log.info("Clientes LinkedIn Jobs: %s", resultado)


@app.timer_trigger(schedule="0 0 14 * * 1", arg_name="timer", run_on_startup=False)
def timer_camara_comercio(timer: func.TimerRequest) -> None:
    """Cámara de Comercio — primer lunes del mes 9:00am Colombia."""
    _ensure_tables()
    resultado = ingest_camara_comercio()
    log.info("Clientes Cámara Comercio: %s", resultado)


@app.timer_trigger(schedule="0 30 14 * * 1", arg_name="timer", run_on_startup=False)
def timer_comunidades_tech(timer: func.TimerRequest) -> None:
    """Comunidades tech — lunes 9:30am Colombia (14:30 UTC)."""
    _ensure_tables()
    resultado = ingest_comunidades_tech()
    log.info("Clientes comunidades tech: %s", resultado)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS — ejecución manual y observabilidad
# ─────────────────────────────────────────────────────────────

@app.route(route="clientes/ejecutar", methods=["GET", "POST"])
def ejecutar(req: func.HttpRequest) -> func.HttpResponse:
    """
    Ejecuta una fuente manualmente.
    GET /api/clientes/ejecutar?fuente=secop_licitaciones
    GET /api/clientes/ejecutar?fuente=todas
    """
    _ensure_tables()
    fuente = req.params.get("fuente", "").lower()

    if not fuente or (fuente not in FUENTES and fuente != "todas"):
        return func.HttpResponse(
            json.dumps({
                "error":   "Parámetro 'fuente' requerido.",
                "opciones": list(FUENTES.keys()) + ["todas"],
            }, ensure_ascii=False),
            status_code=400, mimetype="application/json",
        )

    resultados = []
    targets    = FUENTES.items() if fuente == "todas" else [(fuente, FUENTES[fuente])]

    for nombre_f, fn in targets:
        log.info("Manual clientes: ejecutando %s", nombre_f)
        res = fn()
        resultados.append(res if isinstance(res, dict) else res)
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="clientes/status", methods=["GET"])
def status(req: func.HttpRequest) -> func.HttpResponse:
    """
    Observabilidad del KIT 2.
    GET /api/clientes/status
    GET /api/clientes/status?fuente=secop_licitaciones
    GET /api/clientes/status?modo=blobs
    """
    fuente = req.params.get("fuente", "")
    modo   = req.params.get("modo", "cursores")
    limite = int(req.params.get("limite", "30"))

    try:
        if modo == "blobs":
            blobs = list_recent_blobs(
                CONN_STR, BRONZE_CONTAINER,
                prefix=f"{fuente}/" if fuente else "",
                limite=limite,
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
        return func.HttpResponse(f"Error: {ex}", status_code=500)


@app.route(route="clientes/reset_cursor", methods=["POST"])
def reset(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/clientes/reset_cursor?fuente=secop_licitaciones&empresa=sector_publico
    """
    fuente  = req.params.get("fuente", "")
    empresa = req.params.get("empresa", "")
    if not fuente or not empresa:
        return func.HttpResponse(
            "Parámetros requeridos: fuente y empresa", status_code=400
        )
    try:
        reset_cursor(CONN_STR, fuente, empresa)
        return func.HttpResponse(
            json.dumps({"ok": True,
                        "mensaje": f"Cursor reseteado: {fuente}/{empresa}"},
                       ensure_ascii=False),
            status_code=200, mimetype="application/json",
        )
    except Exception as ex:
        return func.HttpResponse(f"Error: {ex}", status_code=500)
