"""
ingestion_tendencias/function_tendencias.py
===========================================
Azure Function — KIT 3: Tendencias tecnológicas e innovación

KIN que responde:
  1. Tecnologías emergentes en el sector        -> GitHub + RSS + docs + eventos
  2. Tecnologías siendo adoptadas               -> RSS + ThoughtWorks Radar + docs
  3. Nivel de adopción en el mercado            -> GitHub trending + RSS Colombia
  4. Áreas con mayor inversión                  -> noticias/reportes (señal parcial)
  5. Innovaciones registradas por patentes      -> patentes_tech (Gaceta SIC + News)

Fuentes Bronze:
  1. github_trending        — repos trending por tecnología (API GitHub)
  2. rss_fabricantes        — blogs Oracle/Snowflake/Red Hat (RSS + Firecrawl respaldo)
  3. rss_tech_general       — TechCrunch, InfoQ, The New Stack (RSS)
  4. rss_colombia           — enter.co, ITNow LatAm (RSS)
  5. reportes_sectoriales   — ThoughtWorks Radar, AWS blog, Oracle News (Firecrawl)
  6. documentacion_tecnica  — release notes Oracle/Snowflake/Red Hat (Firecrawl)
  7. eventos_tech           — Eventbrite + eventos Oracle/Snowflake (Firecrawl)
  8. patentes_tech          — Gaceta SIC + Google News patentes (KIN 5) [NUEVO]
  9. redes_sociales_tech    — X/Twitter [OPCIONAL, solo HTTP]

Cambios de la auditoría:
  - Eliminado Gartner cloud-strategy de reportes_sectoriales: página de pago que
    solo devuelve marketing (verificado) -> no responde ningún KIN.
  - Eliminado Meetup Bogotá de eventos_tech: página de búsqueda JS de bajo rendimiento.
  - redes_sociales_tech movido FUERA del timer diario (Apify a diario era costoso);
    queda disponible por HTTP.
  - AÑADIDO patentes_tech (KIN 5): cobertura PARCIAL y honesta — la búsqueda
    granular de la SIC es un formulario no scrapeable; se ingiere el índice de la
    Gaceta + noticias de patentes. Limitación documentada de alcance.
  - Docstring de rss_colombia corregido (feeds reales: enter.co / ITNow, no Platzi/MinTIC).

Container Bronze: bronze-tendencias

Triggers:
  Diario  -> GitHub trending + RSS fabricantes (alta volatilidad)
  Semanal -> RSS tech general + RSS Colombia + reportes + docs + eventos + patentes
"""

import os
import sys
import json
import time
import logging
from typing import Optional

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.github_client    import GitHubClient
from shared.news_client      import NewsClient
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
BRONZE_CONTAINER = "bronze-tendencias"

log    = logging.getLogger("ci.tendencias")
app = func.Blueprint()
github = GitHubClient()
news   = NewsClient()
fc     = FirecrawlClient()


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
            variable_ic = "tendencias",
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
# INGESTORES — timer
# ─────────────────────────────────────────────────────────────

def ingest_github_trending() -> dict:
    """
    QUÉ HACE:  repos trending en GitHub para tecnologías del portafolio TAK.
    PARA QUÉ:  KIN 1/3 — proyectos open source ganando tracción = tecnologías
               emergentes y su nivel de adopción.
    Frecuencia: diaria.
    """
    items_raw = github.get_trending_all_topics(dias=30, limite=5)
    return _ingestar(
        fuente     = "github_trending",
        empresa    = "mercado_global",
        items_raw  = items_raw,
        source_url = "https://github.com/trending",
    )


def ingest_rss_fabricantes() -> dict:
    """
    QUÉ HACE:  blogs oficiales de Oracle/Snowflake/Red Hat (RSS + Firecrawl respaldo).
    PARA QUÉ:  KIN 1/2 — novedades de producto de los fabricantes core.
    Frecuencia: diaria.
    """
    items_rss = news.get_feeds_by_group("fabricantes", limite=10)

    urls_fabricantes = [
        "https://blogs.oracle.com/cloud-infrastructure/",
        "https://www.snowflake.com/blog/",
        "https://www.oracle.com/corporate/pressrelease/",
    ]
    items_fc = fc.scrape_many(urls_fabricantes)

    todos = items_rss + items_fc
    return _ingestar(
        fuente     = "rss_fabricantes",
        empresa    = "fabricantes_tak",
        items_raw  = todos,
        source_url = "oracle_snowflake_redhat_blogs",
    )


def ingest_rss_tech_general() -> dict:
    """
    QUÉ HACE:  TechCrunch, InfoQ, The New Stack — tendencias tech globales.
    PARA QUÉ:  KIN 1 — tecnologías emergentes antes de que lleguen a Colombia.
    Frecuencia: semanal.
    """
    items_raw = news.get_feeds_by_group("tech_general", limite=10)
    return _ingestar(
        fuente     = "rss_tech_general",
        empresa    = "mercado_global",
        items_raw  = items_raw,
        source_url = "rss_techcrunch_infoq_thenewstack",
    )


def ingest_rss_colombia() -> dict:
    """
    QUÉ HACE:  feeds tech Colombia/LatAm (enter.co, ITNow LatAm).
    PARA QUÉ:  KIN 2/3 — qué tecnologías adopta el mercado colombiano.
    Frecuencia: semanal.
    """
    items_raw = news.get_feeds_by_group("colombia", limite=10)
    return _ingestar(
        fuente     = "rss_colombia",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "rss_enterco_itnow",
    )


def ingest_reportes_sectoriales() -> dict:
    """
    QUÉ HACE:  ThoughtWorks Technology Radar + AWS Database blog + Oracle News.
    PARA QUÉ:  KIN 1/2 — tendencias estructurales y adopción (el Radar clasifica
               tecnologías en adopt/trial/assess).
    NOTA: se eliminó Gartner (página de pago que solo devuelve marketing).
    Frecuencia: semanal.
    """
    urls = [
        "https://www.thoughtworks.com/radar",
        "https://aws.amazon.com/blogs/database/",
        "https://www.oracle.com/news/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "reportes_sectoriales",
        empresa    = "mercado_global",
        items_raw  = items_raw,
        source_url = "thoughtworks_aws_oracle",
    )


def ingest_documentacion_tecnica() -> dict:
    """
    QUÉ HACE:  documentación/release notes de Oracle/Snowflake/Red Hat.
    PARA QUÉ:  KIN 1/2 — nuevas features y versiones de las tecnologías que TAK
               implementa.
    Frecuencia: semanal.
    """
    urls = [
        "https://docs.oracle.com/en/database/oracle/oracle-database/",
        "https://docs.snowflake.com/en/release-notes",
        "https://docs.redhat.com/en/documentation",
        "https://www.oracle.com/cloud/what-is-cloud-computing/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "documentacion_tecnica",
        empresa    = "fabricantes_tak",
        items_raw  = items_raw,
        source_url = "docs_oracle_snowflake_redhat",
    )


def ingest_eventos_tech() -> dict:
    """
    QUÉ HACE:  eventos/conferencias tech (Eventbrite Colombia + eventos Oracle/Snowflake).
    PARA QUÉ:  KIN 1 — tecnologías emergentes presentadas en eventos del sector.
    NOTA: se eliminó Meetup Bogotá (página de búsqueda JS de bajo rendimiento).
    Frecuencia: semanal.
    """
    urls = [
        "https://www.eventbrite.com/d/colombia--bogot%C3%A1/technology/",
        "https://oracle.com/events/",
        "https://www.snowflake.com/events/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "eventos_tech",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "eventbrite_oracle_snowflake_events",
    )


def ingest_patentes_tech() -> dict:
    """
    QUÉ HACE:  KIN 5 — señal de innovaciones registradas por patentes.
               (a) Índice de la Gaceta de Propiedad Industrial de la SIC (Firecrawl)
               (b) Google News de patentes tecnológicas en Colombia
    PARA QUÉ:  KIN 5 — innovaciones registradas mediante patentes en la industria.

    ⚠️ COBERTURA PARCIAL (limitación documentada):
       La búsqueda granular de patentes de la SIC es un FORMULARIO dinámico
       (serviciospub2.sic.gov.co) que Firecrawl no puede consultar ni paginar.
       Por eso se ingiere el índice de la Gaceta (detecta nuevas publicaciones) y
       noticias de patentes, NO el catálogo completo de patentes. El detalle
       granular quedaría como consulta manual / fuera del alcance automatizable.

    Frecuencia: semanal (las patentes se publican por gaceta, sin urgencia).
    """
    # (a) Índice de la Gaceta / patentes SIC
    urls_sic = [
        "https://www.sic.gov.co/gaceta-oficial-de-la-propiedad-industrial",
        "https://www.sic.gov.co/patentes",
    ]
    items_sic = fc.scrape_many(urls_sic)

    # (b) Noticias de patentes tecnológicas
    temas = [
        "patentes tecnologia Colombia",
        "patente software Colombia",
        "propiedad industrial tecnologia Colombia",
    ]
    items_news = []
    for tema in temas:
        items_news.extend(news.buscar_noticias_empresa(tema, limite=10))
        news.throttle()

    todos = items_sic + items_news
    return _ingestar(
        fuente     = "patentes_tech",
        empresa    = "innovacion_colombia",
        items_raw  = todos,
        source_url = "sic_gaceta + google_news:patentes_colombia",
    )


# ─────────────────────────────────────────────────────────────
# INGESTOR — opcional (solo HTTP, fuera del timer)
# ─────────────────────────────────────────────────────────────

def ingest_redes_sociales_tech() -> dict:
    """
    QUÉ HACE:  X/Twitter — hashtags tech relevantes para TAK.
    ⚠️ OPCIONAL — antes corría a DIARIO con Apify (costoso y ruidoso). Sacado del
       timer; queda por HTTP (?fuente=redes_sociales_tech) para evaluar.
    """
    from shared.apify_client import ApifyClient, ACTOR_X

    apify_client = ApifyClient()
    hashtags = [
        "#Oracle #Colombia",
        "#Snowflake #datos",
        "#CloudMigration #Colombia",
        "#CiberseguridadColombia",
        "#TransformacionDigital #Colombia",
        "#OracleCloud",
    ]

    todos_items = []
    for hashtag in hashtags:
        items = apify_client.run_actor(ACTOR_X, {
            "searchTerms":     [hashtag],
            "maxTweets":       10,
            "sort":            "Latest",
            "withReplies":     False,
            "includeUserInfo": True,
        })
        for item in items:
            item["_hashtag_buscado"] = hashtag
        todos_items.extend(items)
        apify_client.throttle()

    return _ingestar(
        fuente     = "redes_sociales_tech",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "twitter_x_tech_colombia",
    )


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "github_trending":       ingest_github_trending,
    "rss_fabricantes":       ingest_rss_fabricantes,
    "rss_tech_general":      ingest_rss_tech_general,
    "rss_colombia":          ingest_rss_colombia,
    "reportes_sectoriales":  ingest_reportes_sectoriales,
    "documentacion_tecnica": ingest_documentacion_tecnica,
    "eventos_tech":          ingest_eventos_tech,
    "patentes_tech":         ingest_patentes_tech,
    # opcional (solo HTTP)
    "redes_sociales_tech":   ingest_redes_sociales_tech,
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
def timer_tendencias_diario(timer: func.TimerRequest) -> None:
    """GitHub + RSS fabricantes — diario 7:00am Colombia. (Redes ya no va aquí.)"""
    _ensure_tables()
    r1 = ingest_github_trending()
    r2 = ingest_rss_fabricantes()
    log.info("Tendencias diario: github=%s fabricantes=%s", r1, r2)


@app.timer_trigger(schedule="0 0 13 * * 2", arg_name="timer", run_on_startup=False)
def timer_tendencias_semanal(timer: func.TimerRequest) -> None:
    """RSS tech + Colombia + reportes + docs + eventos + patentes — martes 8am Colombia."""
    _ensure_tables()
    r1 = ingest_rss_tech_general()
    r2 = ingest_rss_colombia()
    r3 = ingest_reportes_sectoriales()
    r4 = ingest_documentacion_tecnica()
    r5 = ingest_eventos_tech()
    r6 = ingest_patentes_tech()
    log.info("Tendencias semanal: tech=%s col=%s rep=%s doc=%s ev=%s pat=%s",
             r1, r2, r3, r4, r5, r6)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS
# ─────────────────────────────────────────────────────────────

@app.route(route="tendencias/ejecutar", methods=["GET", "POST"])
def ejecutar_tendencias(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/tendencias/ejecutar?fuente=github_trending
    GET /api/tendencias/ejecutar?fuente=todas
    GET /api/tendencias/ejecutar?fuente=redes_sociales_tech   (opcional)
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

    # 'todas' = todas menos la opcional de redes
    if fuente == "todas":
        targets = [(n, f) for n, f in FUENTES.items() if n != "redes_sociales_tech"]
    else:
        targets = [(fuente, FUENTES[fuente])]

    resultados = []
    for nombre_f, fn in targets:
        log.info("Manual tendencias: ejecutando %s", nombre_f)
        res = fn()
        resultados.append(res)
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="tendencias/status", methods=["GET"])
def status_tendencias(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/tendencias/status
    GET /api/tendencias/status?fuente=github_trending
    GET /api/tendencias/status?modo=blobs
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


@app.route(route="tendencias/reset_cursor", methods=["POST"])
def reset_tendencias(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/tendencias/reset_cursor?fuente=github_trending&empresa=mercado_global"""
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