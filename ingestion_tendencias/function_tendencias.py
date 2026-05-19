"""
ingestion_tendencias/function_tendencias.py
===========================================
Azure Function — KIT 3: Tendencias tecnológicas e innovación

Fuentes Bronze:
  1. GitHub API     → repositorios trending por tecnología (Oracle, Snowflake, cloud)
  2. RSS Fabricantes → blogs Oracle, Snowflake, Red Hat — novedades de proveedores TAK
  3. RSS Tech general → TechCrunch, InfoQ, The New Stack — tendencias globales
  4. RSS Colombia    → Platzi, MinTIC — tendencias tech Colombia/Latam
  5. Firecrawl       → Gartner blog, reportes sectoriales tech

Container Bronze: bronze-tendencias

Triggers:
  Diario  → GitHub trending + RSS fabricantes (alta volatilidad)
  Semanal → RSS tech general + RSS Colombia + Firecrawl reportes

Relevancia para TAK:
  TAK necesita saber por qué las empresas se están cambiando de Microsoft a AWS,
  qué tecnologías están emergiendo, y cuándo Oracle o Snowflake lanzan algo nuevo
  que les permita ampliar su portafolio.
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
app    = func.FunctionApp()
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
        blob_paths    = blob_paths[:20],  # máximo 20 paths para no exceder 64KB en Table Storage
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

def ingest_github_trending() -> dict:
    """
    Repositorios trending en GitHub para tecnologías del portafolio TAK.

    Detecta qué proyectos open source están ganando tracción en:
    Oracle, Snowflake, Kubernetes, cloud migration, data warehouse, etc.

    Frecuencia: diaria — GitHub cambia rápido.
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
    Blogs oficiales de Oracle, Snowflake y Red Hat — proveedores core de TAK.
    RSS donde está disponible, Firecrawl como respaldo.
    Frecuencia: diaria.
    """
    # RSS de fabricantes
    items_rss = news.get_feeds_by_group("fabricantes", limite=10)

    # Firecrawl como respaldo para Oracle y Snowflake
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
    TechCrunch, InfoQ, The New Stack — tendencias tech globales.

    Detecta tendencias antes de que lleguen al mercado colombiano.
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
    Platzi Blog y MinTIC — tendencias tech Colombia/Latam.

    Detecta qué tecnologías están adoptando las empresas colombianas
    y qué políticas tech está promoviendo el gobierno.

    Frecuencia: semanal.
    """
    items_raw = news.get_feeds_by_group("colombia", limite=10)
    return _ingestar(
        fuente     = "rss_colombia",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "rss_platzi_mintic",
    )


def ingest_reportes_sectoriales() -> dict:
    """
    Reportes y blogs tech de referencia — vía Firecrawl.

    Gartner blog, ThoughtWorks Technology Radar, AWS blog.
    Detecta tendencias estructurales del mercado tech.

    Frecuencia: semanal.
    """
    urls = [
        "https://www.gartner.com/en/information-technology/insights/cloud-strategy",
        "https://www.thoughtworks.com/radar",
        "https://aws.amazon.com/blogs/database/",
        "https://www.oracle.com/news/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "reportes_sectoriales",
        empresa    = "mercado_global",
        items_raw  = items_raw,
        source_url = "gartner_thoughtworks_aws_oracle",
    )


def ingest_redes_sociales_tech() -> dict:
    """
    X/Twitter — hashtags tech relevantes para TAK.

    Detecta conversaciones sobre Oracle, Snowflake, cloud, migración
    y tecnologías emergentes en tiempo real.

    Frecuencia: diaria — alta volatilidad.
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


def ingest_documentacion_tecnica() -> dict:
    """
    Documentación oficial de Oracle, Snowflake y Red Hat — vía Firecrawl.

    Detecta nuevas features, versiones y cambios en las tecnologías
    que TAK implementa. Crítico para mantenerse actualizado.

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
    Eventos y conferencias tech Colombia — vía Firecrawl.

    Detecta webinars, conferencias y meetups donde se presentan
    tecnologías emergentes relevantes para TAK.

    Frecuencia: semanal.
    """
    urls = [
        "https://www.meetup.com/es/cities/co/bogota/tech/",
        "https://www.eventbrite.com/d/colombia--bogot%C3%A1/technology/",
        "https://oracle.com/events/",
        "https://www.snowflake.com/events/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "eventos_tech",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "eventos_tech_colombia",
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
    "redes_sociales_tech":   ingest_redes_sociales_tech,
    "documentacion_tecnica": ingest_documentacion_tecnica,
    "eventos_tech":          ingest_eventos_tech,
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
    """GitHub + RSS fabricantes + redes sociales tech — diario 7:00am Colombia."""
    _ensure_tables()
    r1 = ingest_github_trending()
    r2 = ingest_rss_fabricantes()
    r3 = ingest_redes_sociales_tech()
    log.info("Tendencias diario: github=%s fabricantes=%s redes=%s", r1, r2, r3)


@app.timer_trigger(schedule="0 0 13 * * 2", arg_name="timer", run_on_startup=False)
def timer_tendencias_semanal(timer: func.TimerRequest) -> None:
    """RSS tech + Colombia + reportes + docs + eventos — martes 8:00am Colombia."""
    _ensure_tables()
    r1 = ingest_rss_tech_general()
    r2 = ingest_rss_colombia()
    r3 = ingest_reportes_sectoriales()
    r4 = ingest_documentacion_tecnica()
    r5 = ingest_eventos_tech()
    log.info("Tendencias semanal: tech=%s col=%s rep=%s doc=%s ev=%s",
             r1, r2, r3, r4, r5)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS
# ─────────────────────────────────────────────────────────────

@app.route(route="tendencias/ejecutar", methods=["GET", "POST"])
def ejecutar(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/tendencias/ejecutar?fuente=github_trending
    GET /api/tendencias/ejecutar?fuente=todas
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
        log.info("Manual tendencias: ejecutando %s", nombre_f)
        res = fn()
        resultados.append(res)
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="tendencias/status", methods=["GET"])
def status(req: func.HttpRequest) -> func.HttpResponse:
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
def reset(req: func.HttpRequest) -> func.HttpResponse:
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
