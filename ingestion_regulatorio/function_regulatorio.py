"""
ingestion_regulatorio/function_regulatorio.py
=============================================
Azure Function — KIT 6: Entorno regulatorio y financiero tecnológico

Fuentes Bronze:
  1. Firecrawl — MinTIC (normativa, leyes, políticas TI Colombia)
  2. Firecrawl — Colombia Compra Eficiente (regulación compra pública, acuerdos marco)
  3. Firecrawl — Diario Oficial / Función Pública (leyes y decretos)
  4. Firecrawl — Superintendencias (regulación financiera y digital)
  5. Firecrawl — Fuentes de inversión/financiamiento (iNNpulsa, Bancoldex, fondos VC)
  6. RSS — noticias regulatorias y de inversión tech

NOTA: SECOP fue movido a ingestion_clientes (detecta clientes potenciales, no regulación)

Container Bronze: bronze-regulatorio

Frecuencia:
  Mensual    → normativa y regulación (cambia periódicamente)
  Trimestral → políticas públicas e inversión (cambia lentamente)
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
BRONZE_CONTAINER = "bronze-regulatorio"

log  = logging.getLogger("ci.regulatorio")
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
            variable_ic = "regulatorio",
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

def ingest_mintic() -> dict:
    """
    MinTIC — normativa y políticas TI Colombia.
    Detecta nuevas leyes, decretos y programas que afectan a TAK.
    Frecuencia: mensual.
    """
    urls = [
        "https://mintic.gov.co/portal/inicio/",
        "https://mintic.gov.co/portal/inicio/Noticias/",
        "https://mintic.gov.co/portal/inicio/Normativa/",
        "https://mintic.gov.co/portal/inicio/Sala-de-Prensa/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "web_mintic",
        empresa    = "mintic",
        items_raw  = items_raw,
        source_url = "https://mintic.gov.co",
    )


def ingest_colombia_compra() -> dict:
    """
    Colombia Compra Eficiente — regulación de compra pública.
    TAK está en el Acuerdo Marco de Nube Pública — cambios aquí son críticos.
    Frecuencia: mensual.
    """
    urls = [
        "https://www.colombiacompra.gov.co/",
        "https://www.colombiacompra.gov.co/noticias",
        "https://www.colombiacompra.gov.co/tienda-virtual-del-estado-colombiano",
        "https://www.colombiacompra.gov.co/normativa",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "web_colombia_compra",
        empresa    = "colombia_compra_eficiente",
        items_raw  = items_raw,
        source_url = "https://www.colombiacompra.gov.co",
    )


def ingest_normativa_legal() -> dict:
    """
    Función Pública y Diario Oficial — leyes y decretos tech.
    Detecta regulación que puede impactar operación de TAK.
    Frecuencia: mensual.
    """
    urls = [
        "https://www.funcionpublica.gov.co/normativa",
        "https://www.suin-juriscol.gov.co/legislacion/decretos.html",
        "https://www.mintic.gov.co/portal/inicio/Normativa/Decretos/",
        "https://www.mintic.gov.co/portal/inicio/Normativa/Resoluciones/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "normativa_legal",
        empresa    = "gobierno_colombia",
        items_raw  = items_raw,
        source_url = "funcionpublica_mintic_normativa",
    )


def ingest_financiamiento_tech() -> dict:
    """
    Fuentes de financiamiento e inversión tech en Colombia.
    Detecta convocatorias, fondos y programas de apoyo a empresas TI.
    Frecuencia: trimestral.
    """
    urls = [
        "https://www.innpulsacolombia.com/convocatorias",
        "https://www.bancoldex.com/productos-y-servicios/financiacion",
        "https://minciencias.gov.co/convocatorias",
        "https://www.mintic.gov.co/portal/inicio/Convocatorias/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "financiamiento_tech",
        empresa    = "fuentes_financiamiento",
        items_raw  = items_raw,
        source_url = "innpulsa_bancoldex_minciencias_mintic",
    )


def ingest_inversion_tech() -> dict:
    """
    Tendencias de inversión y venture capital en tech Colombia/Latam.
    Incluye reportes financieros formales de inversión.
    Frecuencia: mensual.
    """
    urls = [
        # Noticias y ecosistema VC
        "https://latamlist.com/colombia/",
        "https://www.contxto.com/es/colombia/",
        "https://lavca.org/industry-data/",
        # Reportes financieros formales
        "https://lavca.org/research/",
        "https://www.idbinvest.org/es/sectores/tecnologia",
        "https://www.bancoldex.com/sobre-bancoldex/publicaciones-e-informes",
        "https://innpulsacolombia.com/publicaciones",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "inversion_tech",
        empresa    = "ecosistema_inversion",
        items_raw  = items_raw,
        source_url = "lavca_contxto_idb_bancoldex_innpulsa",
    )


def ingest_rss_regulatorio() -> dict:
    """
    RSS de noticias regulatorias y económicas.
    Detecta cambios normativos antes de que sean oficiales.
    Frecuencia: mensual.
    """
    import feedparser

    feeds = {
        "portafolio_regulacion": "https://www.portafolio.co/rss/feeds.xml",
        "larepublica_economia":  "https://www.larepublica.co/rss/economia",
        "enter_co":              "https://www.enter.co/feed/",
    }

    todos_items = []
    for fuente_rss, url in feeds.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                item = {
                    "title":       entry.get("title", ""),
                    "link":        entry.get("link", ""),
                    "summary":     entry.get("summary", "")[:2000],
                    "published":   entry.get("published", ""),
                    "_fuente_rss": fuente_rss,
                }
                todos_items.append(item)
        except Exception as ex:
            log.error("RSS regulatorio error [%s]: %s", fuente_rss, ex)
        time.sleep(1)

    return _ingestar(
        fuente     = "rss_regulatorio",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "rss_portafolio_larepublica_enter",
    )


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "mintic":              ingest_mintic,
    "colombia_compra":     ingest_colombia_compra,
    "normativa_legal":     ingest_normativa_legal,
    "financiamiento_tech": ingest_financiamiento_tech,
    "inversion_tech":      ingest_inversion_tech,
    "rss_regulatorio":     ingest_rss_regulatorio,
}


def _ensure_tables():
    try:
        init_delta_table(CONN_STR)
    except Exception as ex:
        log.warning("Tablas de control: %s", ex)


# ─────────────────────────────────────────────────────────────
# TRIGGERS — Timer
# ─────────────────────────────────────────────────────────────

@app.timer_trigger(schedule="0 0 14 1 * *", arg_name="timer", run_on_startup=False)
def timer_regulatorio_mensual(timer: func.TimerRequest) -> None:
    """MinTIC + CCE + normativa + RSS + inversión — día 1 de cada mes 9am Colombia."""
    _ensure_tables()
    r1 = ingest_mintic()
    r2 = ingest_colombia_compra()
    r3 = ingest_normativa_legal()
    r4 = ingest_rss_regulatorio()
    r5 = ingest_inversion_tech()
    log.info("Regulatorio mensual: mintic=%s cce=%s norm=%s rss=%s inv=%s",
             r1, r2, r3, r4, r5)


@app.timer_trigger(schedule="0 0 14 1 1,4,7,10 *", arg_name="timer", run_on_startup=False)
def timer_regulatorio_trimestral(timer: func.TimerRequest) -> None:
    """Financiamiento — trimestral (ene, abr, jul, oct) 9am Colombia."""
    _ensure_tables()
    resultado = ingest_financiamiento_tech()
    log.info("Regulatorio trimestral: financiamiento=%s", resultado)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS
# ─────────────────────────────────────────────────────────────

@app.route(route="regulatorio/ejecutar", methods=["GET", "POST"])
def ejecutar(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/regulatorio/ejecutar?fuente=mintic
    GET /api/regulatorio/ejecutar?fuente=todas
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
        log.info("Manual regulatorio: ejecutando %s", nombre_f)
        res = fn()
        resultados.append(res)
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="regulatorio/status", methods=["GET"])
def status(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/regulatorio/status
    GET /api/regulatorio/status?fuente=web_mintic
    GET /api/regulatorio/status?modo=blobs
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


@app.route(route="regulatorio/reset_cursor", methods=["POST"])
def reset(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/regulatorio/reset_cursor?fuente=web_mintic&empresa=mintic"""
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
