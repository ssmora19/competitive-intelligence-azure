"""
ingestion_proveedores/function_proveedores.py
=============================================
Azure Function — KIT 4: Ecosistema de proveedores tecnológicos

Fuentes Bronze:
  1. Firecrawl — Web proveedores core TAK (Oracle, Snowflake, IBM, Dell, MS, Red Hat)
     → Detecta cambios de portafolio, nuevas soluciones, precios
  2. Firecrawl — Marketplaces tech (AWS, Azure, Oracle Cloud Marketplace)
     → Detecta nuevos proveedores entrando al ecosistema cloud
  3. Firecrawl — Licenciamiento (páginas de precios y términos)
     → Detecta cambios en modelos de licencia que afectan a TAK
  4. Firecrawl — Startups tech Colombia (Endeavor, iNNpulsa)
     → Detecta nuevos actores entrando al mercado colombiano
  5. RSS noticias proveedores
     → Novedades de fabricantes (reutiliza news_client de KIT 3)

Container Bronze: bronze-proveedores
Frecuencia: mensual — los portafolios y licencias cambian lentamente
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
BRONZE_CONTAINER = "bronze-proveedores"

log  = logging.getLogger("ci.proveedores")
app  = func.FunctionApp()
fc   = FirecrawlClient()
news = NewsClient()


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
            variable_ic = "proveedores",
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

def ingest_web_proveedores_core() -> dict:
    """
    Webs de los fabricantes/proveedores core de TAK.

    TAK es partner de Oracle, IBM, Dell, Microsoft, Red Hat, Snowflake.
    Monitorea cambios en sus portafolios, nuevas soluciones y alianzas.

    Frecuencia: mensual.
    """
    urls = [
        # Oracle — principal fabricante de TAK
        "https://www.oracle.com/co/",
        "https://www.oracle.com/co/partners/",
        "https://www.oracle.com/cloud/",

        # Snowflake
        "https://www.snowflake.com/en/partners/",
        "https://www.snowflake.com/en/why-snowflake/",

        # IBM
        "https://www.ibm.com/co-es/",
        "https://www.ibm.com/partnerworld/",

        # Red Hat
        "https://www.redhat.com/es/partners",
        "https://www.redhat.com/es/technologies",

        # Dell
        "https://www.dell.com/es-co/dt/solutions/index.htm",

        # Microsoft
        "https://www.microsoft.com/es-co/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "web_proveedores_core",
        empresa    = "fabricantes_tak",
        items_raw  = items_raw,
        source_url = "oracle_snowflake_ibm_redhat_dell_microsoft",
    )


def ingest_marketplaces_tech() -> dict:
    """
    Marketplaces cloud — nuevos proveedores entrando al ecosistema.

    AWS, Azure y Oracle Cloud Marketplace son donde los nuevos
    proveedores de software publican sus soluciones.
    Detecta nuevos actores antes de que lleguen al mercado colombiano.

    Frecuencia: mensual.
    """
    urls = [
        "https://aws.amazon.com/marketplace/search/results?searchTerms=colombia",
        "https://azuremarketplace.microsoft.com/es-co/marketplace/",
        "https://cloudmarketplace.oracle.com/marketplace/",
        "https://www.salesforce.com/products/platform/appexchange/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "marketplaces_tech",
        empresa    = "ecosistema_cloud",
        items_raw  = items_raw,
        source_url = "aws_azure_oracle_marketplace",
    )


def ingest_licenciamiento() -> dict:
    """
    Páginas de precios y licenciamiento de fabricantes core de TAK.

    Detecta cambios en modelos de licencia que afectan directamente
    el portafolio y la rentabilidad de TAK.
    Crítico: Oracle cambia precios frecuentemente.

    Frecuencia: mensual.
    """
    urls = [
        "https://www.oracle.com/co/cloud/pricing/",
        "https://www.snowflake.com/en/data-cloud/pricing-options/",
        "https://azure.microsoft.com/es-co/pricing/",
        "https://www.redhat.com/es/technologies/linux-platforms/enterprise-linux/licensing",
        "https://www.ibm.com/co-es/products/software",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "licenciamiento_fabricantes",
        empresa    = "fabricantes_tak",
        items_raw  = items_raw,
        source_url = "precios_oracle_snowflake_azure_redhat_ibm",
    )


def ingest_startups_tech_colombia() -> dict:
    """
    Startups tech Colombia — nuevos actores entrando al mercado.

    iNNpulsa y Endeavor son las principales organizaciones que
    apoyan startups tech en Colombia. Detectar nuevos competidores
    antes de que se vuelvan relevantes.

    Frecuencia: mensual.
    """
    urls = [
        "https://www.innpulsacolombia.com/noticias",
        "https://endeavor.org.co/emprendedores/",
        "https://apps.co/",
        "https://www.mintic.gov.co/portal/inicio/Noticias/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "startups_tech_colombia",
        empresa    = "ecosistema_colombia",
        items_raw  = items_raw,
        source_url = "innpulsa_endeavor_appsco_mintic",
    )


def ingest_rss_proveedores() -> dict:
    """
    RSS de noticias de proveedores — novedades de fabricantes.
    Reutiliza los feeds de fabricantes configurados en KIT 3.
    Frecuencia: mensual.
    """
    items_raw = news.get_feeds_by_group("fabricantes", limite=20)
    return _ingestar(
        fuente     = "rss_proveedores",
        empresa    = "fabricantes_tak",
        items_raw  = items_raw,
        source_url = "rss_fabricantes_tak",
    )


def ingest_redes_sociales_proveedores() -> dict:
    """
    X/Twitter — novedades de fabricantes y nuevos proveedores tech.

    Detecta anuncios de productos, alianzas y cambios de estrategia
    de Oracle, Snowflake, IBM, Red Hat y Microsoft en Latam.

    Frecuencia: mensual.
    """
    from shared.apify_client import ApifyClient, ACTOR_X
    apify_client = ApifyClient()

    hashtags = [
        "#OracleCloud Latam",
        "#Snowflake Colombia",
        "#IBMLatam tecnologia",
        "#RedHat Colombia",
        "#MicrosoftAzure Colombia",
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
        fuente     = "redes_sociales_proveedores",
        empresa    = "fabricantes_tak",
        items_raw  = todos_items,
        source_url = "twitter_fabricantes_latam",
    )


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "web_proveedores_core":         ingest_web_proveedores_core,
    "marketplaces_tech":            ingest_marketplaces_tech,
    "licenciamiento":               ingest_licenciamiento,
    "startups_tech_colombia":       ingest_startups_tech_colombia,
    "rss_proveedores":              ingest_rss_proveedores,
    "redes_sociales_proveedores":   ingest_redes_sociales_proveedores,
}


def _ensure_tables():
    try:
        init_delta_table(CONN_STR)
    except Exception as ex:
        log.warning("Tablas de control: %s", ex)


# ─────────────────────────────────────────────────────────────
# TRIGGERS — Timer (mensual — primer lunes de cada mes)
# ─────────────────────────────────────────────────────────────

@app.timer_trigger(schedule="0 0 14 1 * *", arg_name="timer", run_on_startup=False)
def timer_proveedores_mensual(timer: func.TimerRequest) -> None:
    """
    Todas las fuentes de proveedores — día 1 de cada mes 9:00am Colombia (14:00 UTC).
    Mensual porque los portafolios y licencias cambian lentamente.
    """
    _ensure_tables()
    resultados = {}
    for nombre_f, fn in FUENTES.items():
        resultados[nombre_f] = fn()
        time.sleep(2)
    log.info("Proveedores mensual: %s", resultados)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS
# ─────────────────────────────────────────────────────────────

@app.route(route="proveedores/ejecutar", methods=["GET", "POST"])
def ejecutar(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/proveedores/ejecutar?fuente=web_proveedores_core
    GET /api/proveedores/ejecutar?fuente=todas
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
        log.info("Manual proveedores: ejecutando %s", nombre_f)
        res = fn()
        resultados.append(res)
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="proveedores/status", methods=["GET"])
def status(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/proveedores/status
    GET /api/proveedores/status?fuente=web_proveedores_core
    GET /api/proveedores/status?modo=blobs
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


@app.route(route="proveedores/reset_cursor", methods=["POST"])
def reset(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/proveedores/reset_cursor?fuente=web_proveedores_core&empresa=fabricantes_tak"""
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
