"""
ingestion_proveedores/function_proveedores.py
=============================================
Azure Function — KIT 4: Ecosistema de proveedores tecnológicos

Partners reales de TAK vigilados:
  Oracle, IBM, AWS, Microsoft, Dell, TD SYNNEX, Fortinet, Red Hat, Nexsys.
  Snowflake incluido (partner confirmado por gerencia).

KIN que responde:
  1. ¿Qué nuevos proveedores están entrando con soluciones relevantes?  (Google News)
  2. ¿Qué nuevas tecnologías/soluciones incorporan los proveedores?      (web + noticias + RSS)
  3. ¿Qué cambios hay en los modelos de licenciamiento?                  (licenciamiento)
  4. (Gold) estrategias para convertir no-clientes en clientes -> NO es Bronze.

Diseño (mejora de trazabilidad):
  Cada partner se ingiere con SU PROPIA etiqueta empresa (Oracle, AWS, Fortinet…),
  no bajo un genérico "fabricantes_tak". Así Gold puede comparar por partner.
  Las URLs viven en el catálogo PROVEEDORES, organizadas por categoría:
    web       -> portafolio / soluciones / partners  (Firecrawl)  KIN 2
    licencia  -> precios / licenciamiento            (Firecrawl)  KIN 3
    noticias  -> salas de prensa / newsroom          (Firecrawl)  KIN 2

Notas de la auditoría:
  - URLs de AWS limpiadas de parámetros de rastreo (gclid/trk/gads); descartada
    la landing free/webapps (era anuncio).
  - TD SYNNEX y Nexsys son DISTRIBUIDORES (no fabricantes): su "web" es qué marcas
    distribuyen -> sirve a KIN 1 y 2.
  - Varias páginas son catálogos JS paginados (Oracle marketplace, IBM, Dell
    FindAPartner): Firecrawl trae solo la primera vista, no el catálogo completo.
  - Este módulo NO usa build_company_urls; el recorte de WEB_PATHS no lo afecta.

Container Bronze: bronze-proveedores
Frecuencia: mensual — portafolios y licencias cambian lentamente.
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
app = func.Blueprint()
fc   = FirecrawlClient()
news = NewsClient()


# ─────────────────────────────────────────────────────────────
# CATÁLOGO DE PROVEEDORES  (URLs verificadas por el usuario)
# ─────────────────────────────────────────────────────────────
PROVEEDORES = {
    "Oracle": {
        "web": [
            "https://www.oracle.com/co/",
            "https://marketplace.oracle.com/partners",
            "https://www.oracle.com/cloud/",
        ],
        "licencia": ["https://www.oracle.com/co/cloud/pricing/"],
        "noticias": ["https://www.oracle.com/news/"],
    },
    "IBM": {
        "web": [
            "https://www.ibm.com/co-es/",
            "https://www.ibm.com/es-es/partnerplus/services",
        ],
        "licencia": ["https://www.ibm.com/co-es/products/software"],
        "noticias": ["https://www.ibm.com/es-es/new"],
    },
    "AWS": {
        "web": [
            "https://aws.amazon.com/es/partners/work-with-partners/",
            "https://aws.amazon.com/es/solutions/manufacturing/",
            "https://aws.amazon.com/es/professional-services/",
            "https://aws.amazon.com/es/business-applications/",
            "https://aws.amazon.com/es/isv/saas-scaling-and-partnership/",
        ],
        "licencia": ["https://aws.amazon.com/es/pricing/"],
        "noticias": ["https://aws.amazon.com/es/new/"],
    },
    "Microsoft": {
        "web": ["https://www.microsoft.com/es-co/"],
        "licencia": [
            "https://www.microsoft.com/es-co/microsoft-365/business/microsoft-365-plans-and-pricing",
        ],
        "noticias": ["https://news.microsoft.com/source/latam/ultimas-noticias/"],
    },
    "Dell": {
        "web": [
            "https://dell.my.site.com/FindAPartner/s/partnersearch?language=es&country=co",
            "https://www.dell.com/es-es/blog/categories/solutions-services/",
        ],
        "licencia": [],
        "noticias": [],  # el portal de Dell no publica sala de prensa
    },
    "TD SYNNEX": {  # distribuidor
        "web": [
            "https://www.tdsynnex.com/na/us/vendors/",
            "https://www.tdsynnex.com/na/us/advancedsolutions/",
            "https://www.tdsynnex.com/na/us/destination-ai/",
            "https://www.tdsynnex.com/na/us/consumer/",
        ],
        "licencia": [],
        "noticias": ["https://news.tdsynnex.com/"],
    },
    "Fortinet": {
        "web": [
            "https://www.fortinet.com/lat/solutions/network-security",
            "https://www.fortinet.com/lat/solutions/ai-security",
            "https://www.fortinet.com/lat/solutions/unified-sase",
            "https://www.fortinet.com/lat/solutions/security-operations",
            "https://www.fortinet.com/lat/partners/partnerships/alliance-partners",
        ],
        "licencia": [],
        "noticias": ["https://www.fortinet.com/lat/corporate/about-us/newsroom"],
    },
    "Red Hat": {
        "web": [
            "https://www.redhat.com/es/partners/certified-cloud-and-service-providers",
            "https://www.redhat.com/es/partners/isv",
            "https://catalog.redhat.com/en",
            "https://www.redhat.com/es/technologies",
        ],
        "licencia": ["https://www.redhat.com/es/about/eulas"],
        "noticias": [],  # cubierto por RSS (redhat.com/en/rss/blog)
    },
    "Nexsys": {  # distribuidor
        "web": [
            "https://www.nexsysla.com/co/fabricantes/",
            "https://www.nexsysla.com/co/cloud/portafolio/",
            "https://www.nexsysla.com/co/promociones/",
        ],
        "licencia": [],
        "noticias": ["https://www.nexsysla.com/co/noticias/"],
    },

    "Snowflake": {  # partner confirmado por gerencia (voz a voz)
        "web": [
            "https://www.snowflake.com/en/why-snowflake/partners/all-partners/",
            "https://www.snowflake.com/en/why-snowflake/",
        ],
        "licencia": ["https://www.snowflake.com/en/data-cloud/pricing-options/"],
        "noticias": [
            "https://www.snowflake.com/en/news/news-coverage/",
            "https://www.snowflake.com/en/news/press-releases/",
        ],
    },
}


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
# INGESTORES — timer  (iteran el catálogo PROVEEDORES por partner)
# ─────────────────────────────────────────────────────────────

def _ingestar_categoria(fuente: str, categoria: str) -> list[dict]:
    """
    Motor común: para cada partner con URLs en `categoria`, scrapea con
    Firecrawl y guarda bajo empresa = nombre del partner (trazabilidad).
    """
    resultados = []
    for nombre, cfg in PROVEEDORES.items():
        urls = cfg.get(categoria) or []
        if not urls:
            continue
        try:
            items_raw = fc.scrape_many(urls)   # el filtro statusCode ya bota 404s
        except Exception as ex:
            log.warning("[%s/%s] scrape falló: %s", fuente, nombre, ex)
            items_raw = []
        resultados.append(_ingestar(
            fuente     = fuente,
            empresa    = nombre,
            items_raw  = items_raw,
            source_url = urls[0],
        ))
        fc.throttle()
    return resultados


def ingest_web_proveedores_core() -> list[dict]:
    """
    QUÉ HACE:  scrapea portafolio/soluciones/partners de cada proveedor.
    PARA QUÉ:  KIN 2 — nuevas tecnologías/soluciones en su portafolio.
    Frecuencia: mensual.
    """
    return _ingestar_categoria("web_proveedores_core", "web")


def ingest_licenciamiento() -> list[dict]:
    """
    QUÉ HACE:  scrapea páginas de precios/licenciamiento por proveedor.
    PARA QUÉ:  KIN 3 — cambios en modelos de licencia.
    NOTA: pricing suele ser muy dinámico (JS); verifica contenido real.
    Frecuencia: mensual.
    """
    return _ingestar_categoria("licenciamiento_fabricantes", "licencia")


def ingest_noticias_proveedores() -> list[dict]:
    """
    QUÉ HACE:  scrapea con Firecrawl las salas de prensa/newsroom por proveedor
               (Oracle, AWS, TD SYNNEX, Fortinet, Nexsys, Snowflake).
    PARA QUÉ:  KIN 2 — anuncios corporativos, alianzas, lanzamientos.
    Complementa a rss_proveedores (feeds técnicos): esto es la prensa oficial.
    Frecuencia: mensual.
    """
    return _ingestar_categoria("noticias_proveedores", "noticias")


def ingest_rss_proveedores() -> dict:
    """
    QUÉ HACE:  lee los RSS de fabricantes (grupo 'fabricantes' del news_client:
               Oracle DB, Snowflake engineering, Red Hat blog).
    PARA QUÉ:  KIN 2 — novedades técnicas de producto vía feed.
    Frecuencia: mensual.
    """
    items_raw = news.get_feeds_by_group("fabricantes", limite=20)
    return _ingestar(
        fuente     = "rss_proveedores",
        empresa    = "fabricantes_tak",
        items_raw  = items_raw,
        source_url = "rss_fabricantes_tak",
    )


def ingest_startups_tech_colombia() -> dict:
    """
    QUÉ HACE:  Google News por temas de NUEVOS proveedores/soluciones TI en
               Colombia (reusa news_client.buscar_noticias_empresa).
    PARA QUÉ:  KIN 1 — nuevos proveedores entrando al mercado.
    Reemplaza el scraping de iNNpulsa/Endeavor/apps.co (rutas supuestas + ruido).
    Frecuencia: mensual.
    """
    temas = [
        "nueva empresa software Colombia",
        "startup tecnologia B2B Colombia",
        "proveedor nube Colombia lanzamiento",
        "empresa tecnologia entra mercado Colombia",
        "solucion cloud datos empresas Colombia",
    ]
    todos_items = []
    for tema in temas:
        todos_items.extend(news.buscar_noticias_empresa(tema, limite=10))
        news.throttle()

    return _ingestar(
        fuente     = "startups_tech_colombia",
        empresa    = "ecosistema_colombia",
        items_raw  = todos_items,
        source_url = "google_news:nuevos_proveedores_colombia",
    )


# ─────────────────────────────────────────────────────────────
# INGESTORES — opcionales (solo HTTP, fuera del timer)
# ─────────────────────────────────────────────────────────────

def ingest_marketplaces_tech() -> dict:
    """
    QUÉ HACE:  scrapea marketplaces cloud (AWS, Azure, Oracle, Salesforce).
    ⚠️ OPCIONAL — páginas de búsqueda JS de bajo rendimiento; Google News cubre
       mejor el KIN 1. Queda por HTTP para evaluar.
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


def ingest_redes_sociales_proveedores() -> dict:
    """
    QUÉ HACE:  X/Twitter — novedades de fabricantes por hashtags.
    ⚠️ OPCIONAL — consume Apify y es ruidoso; fuera del timer.
    """
    from shared.apify_client import ApifyClient, ACTOR_X
    apify_client = ApifyClient()

    hashtags = [
        "#OracleCloud Latam", "#Fortinet Colombia", "#AWS Colombia",
        "#RedHat Colombia", "#MicrosoftAzure Colombia",
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
    "licenciamiento":               ingest_licenciamiento,
    "noticias_proveedores":         ingest_noticias_proveedores,
    "rss_proveedores":              ingest_rss_proveedores,
    "startups_tech_colombia":       ingest_startups_tech_colombia,
    # opcionales (solo HTTP)
    "marketplaces_tech":            ingest_marketplaces_tech,
    "redes_sociales_proveedores":   ingest_redes_sociales_proveedores,
}

FUENTES_TIMER = [
    "web_proveedores_core",
    "licenciamiento",
    "noticias_proveedores",
    "rss_proveedores",
    "startups_tech_colombia",
]


def _ensure_tables():
    try:
        init_delta_table(CONN_STR)
    except Exception as ex:
        log.warning("Tablas de control: %s", ex)


def _aplanar(res):
    """Unos ingestores devuelven dict, otros lista de dicts."""
    return res if isinstance(res, list) else [res]


# ─────────────────────────────────────────────────────────────
# TRIGGERS — Timer (mensual — día 1 de cada mes)
# ─────────────────────────────────────────────────────────────

@app.timer_trigger(schedule="0 0 14 1 * *", arg_name="timer", run_on_startup=False)
def timer_proveedores_mensual(timer: func.TimerRequest) -> None:
    """
    Fuentes core de proveedores — día 1 de cada mes 9:00am Colombia (14:00 UTC).
    marketplaces y redes quedan solo por HTTP.
    """
    _ensure_tables()
    resumen = {}
    for nombre_f in FUENTES_TIMER:
        res = FUENTES[nombre_f]()
        resumen[nombre_f] = len(_aplanar(res))
        time.sleep(2)
    log.info("Proveedores mensual: %s", resumen)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS
# ─────────────────────────────────────────────────────────────

@app.route(route="proveedores/ejecutar", methods=["GET", "POST"])
def ejecutar_proveedores(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/proveedores/ejecutar?fuente=web_proveedores_core
    GET /api/proveedores/ejecutar?fuente=todas          (solo las del timer)
    GET /api/proveedores/ejecutar?fuente=marketplaces_tech   (opcional)
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
    if fuente == "todas":
        targets = [(n, FUENTES[n]) for n in FUENTES_TIMER]
    else:
        targets = [(fuente, FUENTES[fuente])]

    for nombre_f, fn in targets:
        log.info("Manual proveedores: ejecutando %s", nombre_f)
        resultados.extend(_aplanar(fn()))
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="proveedores/status", methods=["GET"])
def status_proveedores(req: func.HttpRequest) -> func.HttpResponse:
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
def reset_proveedores(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/proveedores/reset_cursor?fuente=web_proveedores_core&empresa=Oracle"""
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