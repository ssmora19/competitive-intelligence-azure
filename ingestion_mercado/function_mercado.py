"""
ingestion_mercado/function_mercado.py
=====================================
Azure Function — KIT 5: Comportamiento del mercado tecnológico

Preguntas que responde:
  1. ¿Qué segmentos del mercado presentan mayor crecimiento?
  2. ¿Qué necesidades no satisfechas existen en el mercado?
  3. ¿Qué regiones o sectores presentan oportunidades de expansión?

Fuentes Bronze:
  1. DANE — estadísticas económicas Colombia (API pública gratuita)
  2. Banco de la República — reportes económicos (Firecrawl)
  3. Reportes sectoriales — MinTIC, Fedesoft, Colombia TIC (Firecrawl)
  4. RSS noticias económicas — portafolio, dinero, la república (RSS)
  5. Redes sociales mercado — X/Twitter con hashtags de mercado TI Colombia
  6. Estudios de mercado — IDC, Gartner Latam (Firecrawl)

Container Bronze: bronze-mercado

Frecuencia:
  Trimestral → reportes económicos y estudios de mercado
  Mensual    → noticias económicas y redes sociales
"""

import os
import sys
import json
import time
import logging

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.firecrawl_client import FirecrawlClient
from shared.news_client      import NewsClient
from shared.apify_client     import ApifyClient, ACTOR_X
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
BRONZE_CONTAINER = "bronze-mercado"

log   = logging.getLogger("ci.mercado")
app   = func.FunctionApp()
fc    = FirecrawlClient()
news  = NewsClient()
apify = ApifyClient()


# ─────────────────────────────────────────────────────────────
# ORQUESTADOR DELTA
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
            variable_ic = "mercado",
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
        blob_paths    = blob_paths[:20],
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

def ingest_dane_estadisticas() -> dict:
    """
    DANE — estadísticas TIC de Colombia vía Firecrawl.

    Detecta: adopción de tecnología en empresas colombianas,
    cobertura digital, inversión en TIC por sector.

    Frecuencia: trimestral.
    """
    urls = [
        "https://www.dane.gov.co/index.php/estadisticas-por-tema/tecnologia-e-innovacion/tecnologias-de-la-informacion-y-las-comunicaciones-tic",
        "https://www.dane.gov.co/index.php/estadisticas-por-tema/tecnologia-e-innovacion/tecnologias-de-la-informacion-y-las-comunicaciones-tic/indicadores-basicos-de-tic-en-empresas",
        "https://www.mintic.gov.co/portal/inicio/Sala-de-Prensa/Noticias/",
        "https://colombiatic.mintic.gov.co/estadisticas/stats.php",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "dane_estadisticas",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "dane_gov_co_tic",
    )


def ingest_reportes_economicos() -> dict:
    """
    Reportes económicos del Banco de la República y entidades oficiales.

    Detecta: tendencias macroeconómicas, inversión tech en Colombia,
    sectores con mayor crecimiento.

    Frecuencia: trimestral.
    """
    urls = [
        "https://www.banrep.gov.co/es/estadisticas/economia-digital",
        "https://www.dnp.gov.co/programas/desarrollo-empresarial/tic",
        "https://www.colombiaproductiva.com/ptp-sectores/servicios/software-y-ti",
        "https://fedesoft.org/noticias/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "reportes_economicos",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "banrep_dnp_fedesoft",
    )


def ingest_estudios_mercado_ti() -> dict:
    """
    Estudios de mercado TI — IDC, Gartner Latam, Everest Group.

    Detecta: tamaño del mercado, forecast de crecimiento,
    segmentos con mayor demanda en Latam y Colombia.

    Frecuencia: trimestral.
    """
    urls = [
        "https://www.idc.com/getdoc.jsp?containerId=prLA52230624",
        "https://www.gartner.com/en/information-technology/insights/it-spending-forecast",
        "https://www.everestgrp.com/locations/latin-america/",
        "https://www.mintic.gov.co/portal/inicio/Sala-de-Prensa/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "estudios_mercado_ti",
        empresa    = "mercado_global",
        items_raw  = items_raw,
        source_url = "idc_gartner_everest_mintic",
    )


def ingest_noticias_economicas() -> dict:
    """
    RSS de medios económicos colombianos.

    Detecta: noticias de inversión, contratos tech, expansión de empresas,
    licitaciones relevantes para TAK.

    Frecuencia: mensual.
    """
    import feedparser

    feeds_economicos = {
        "portafolio":    "https://www.portafolio.co/rss/feeds.xml",
        "dinero":        "https://www.dinero.com/rss/tecnologia.xml",
        "la_republica":  "https://www.larepublica.co/rss/tecnologia",
        "enter_co":      "https://www.enter.co/feed/",
    }

    todos_items = []
    for fuente_rss, url in feeds_economicos.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                item = {
                    "title":       entry.get("title", ""),
                    "link":        entry.get("link", ""),
                    "summary":     entry.get("summary", "")[:2000],
                    "published":   entry.get("published", ""),
                    "_fuente_rss": fuente_rss,
                    "_url_feed":   url,
                }
                todos_items.append(item)
            log.info("RSS económico: %d items de %s", len(feed.entries[:10]), fuente_rss)
        except Exception as ex:
            log.error("RSS económico error [%s]: %s", fuente_rss, ex)
        time.sleep(1)

    return _ingestar(
        fuente     = "noticias_economicas",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "portafolio_dinero_larepublica_enter",
    )


def ingest_redes_sociales_mercado() -> dict:
    """
    X/Twitter — conversaciones sobre mercado TI Colombia.

    Detecta: necesidades no satisfechas, sectores buscando tech,
    oportunidades de expansión mencionadas en redes.

    Frecuencia: mensual.
    """
    hashtags = [
        "#TransformacionDigital #Colombia",
        "#MercadoTI #Colombia",
        "#TecnologiaEmpresarial #Colombia",
        "#InversionTecnologia #Colombia",
        "#SectorPublico #tecnologia Colombia",
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
        fuente     = "redes_sociales_mercado",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "twitter_mercado_ti_colombia",
    )


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "dane_estadisticas":       ingest_dane_estadisticas,
    "reportes_economicos":     ingest_reportes_economicos,
    "estudios_mercado_ti":     ingest_estudios_mercado_ti,
    "noticias_economicas":     ingest_noticias_economicas,
    "redes_sociales_mercado":  ingest_redes_sociales_mercado,
}


def _ensure_tables():
    try:
        init_delta_table(CONN_STR)
    except Exception as ex:
        log.warning("Tablas de control: %s", ex)


# ─────────────────────────────────────────────────────────────
# TRIGGERS — Timer
# ─────────────────────────────────────────────────────────────

@app.timer_trigger(schedule="0 0 14 1 1,4,7,10 *", arg_name="timer", run_on_startup=False)
def timer_mercado_trimestral(timer: func.TimerRequest) -> None:
    """
    DANE + reportes + estudios — trimestral (1 ene, 1 abr, 1 jul, 1 oct) 9am Colombia.
    """
    _ensure_tables()
    r1 = ingest_dane_estadisticas()
    r2 = ingest_reportes_economicos()
    r3 = ingest_estudios_mercado_ti()
    log.info("Mercado trimestral: dane=%s reportes=%s estudios=%s", r1, r2, r3)


@app.timer_trigger(schedule="0 0 14 1 * *", arg_name="timer", run_on_startup=False)
def timer_mercado_mensual(timer: func.TimerRequest) -> None:
    """Noticias económicas + redes — mensual día 1 9am Colombia."""
    _ensure_tables()
    r1 = ingest_noticias_economicas()
    r2 = ingest_redes_sociales_mercado()
    log.info("Mercado mensual: noticias=%s redes=%s", r1, r2)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS
# ─────────────────────────────────────────────────────────────

@app.route(route="mercado/ejecutar", methods=["GET", "POST"])
def ejecutar(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/mercado/ejecutar?fuente=dane_estadisticas
    GET /api/mercado/ejecutar?fuente=todas
    """
    _ensure_tables()
    fuente = req.params.get("fuente", "").lower()

    if not fuente or (fuente not in FUENTES and fuente != "todas"):
        return func.HttpResponse(
            json.dumps({"error": "Parámetro 'fuente' requerido.",
                        "opciones": list(FUENTES.keys()) + ["todas"]},
                       ensure_ascii=False),
            status_code=400, mimetype="application/json",
        )

    resultados = []
    targets    = FUENTES.items() if fuente == "todas" else [(fuente, FUENTES[fuente])]

    for nombre_f, fn in targets:
        log.info("Manual mercado: ejecutando %s", nombre_f)
        res = fn()
        resultados.append(res)
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="mercado/status", methods=["GET"])
def status(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/mercado/status
    GET /api/mercado/status?fuente=dane_estadisticas
    GET /api/mercado/status?modo=blobs
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


@app.route(route="mercado/reset_cursor", methods=["POST"])
def reset(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/mercado/reset_cursor?fuente=dane_estadisticas&empresa=mercado_colombia"""
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
