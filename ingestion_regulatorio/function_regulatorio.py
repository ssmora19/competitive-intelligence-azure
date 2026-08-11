"""
ingestion_regulatorio/function_regulatorio.py
=============================================
Azure Function — KIT 6: Entorno regulatorio y financiero tecnológico

KIN que responde:
  1. Regulaciones de datos/privacidad/servicios TI    -> SIC + normativa TIC + CCE
  2. Políticas públicas / programas gubernamentales    -> MinTIC + noticias
  3. Tendencias de inversión                           -> inversion_tech + noticias
  4. Oportunidades de financiamiento                   -> financiamiento_tech
  5. Condiciones de acceso a financiamiento            -> financiamiento + noticias
  6. Inversión de competidores/startups                -> inversion_tech + Google News

Fuentes Bronze (auditadas y verificadas contra los KIN):
  BLOQUE REGULATORIO
    1. mintic          — portal MinTIC (políticas, noticias)        -> KIN 2
    2. normativa_tic   — normograma.mintic.gov.co (decretos/boletines) -> KIN 1
    3. sic_datos       — SIC protección de datos (Ley 1581/Hábeas Data) -> KIN 1
    4. colombia_compra — Colombia Compra Eficiente (compra pública)  -> KIN 1,2
  BLOQUE FINANCIERO
    5. financiamiento_tech — Minciencias + MinTIC convocatorias      -> KIN 4,5
    6. inversion_tech      — LatamList, Contxto, LAVCA               -> KIN 3,6
    7. noticias_regulatorio— Google News (regulación + inversión)    -> KIN 1..6

Cambios de la auditoría:
  - AÑADIDA la SIC (protección de datos): faltaba, y es LA autoridad del KIN 1
    (datos/privacidad) bajo la Ley 1581 de 2012. Hueco cerrado.
  - MinTIC normativa corregida: de /portal/inicio/Normativa/ (ruta supuesta) a
    normograma.mintic.gov.co (portal jurídico real del sector TIC).
  - ELIMINADO normativa_legal: funcionpublica/normativa y suin-juriscol/decretos
    no servían (verificado por el usuario); su normativa la cubre normograma.
  - inversion_tech: quitadas las URLs que no cargaban (idbinvest, bancoldex e
    innpulsa de inversión). Quedan LatamList, Contxto, LAVCA.
  - financiamiento_tech: iNNpulsa y Bancóldex reincorporados con sus URLs REALES
    verificadas (convocatorias.innpulsacolombia.com, bancoldex portafolio/noticias).
  - rss_regulatorio (RSS hardcodeados, feeds muertos) -> migrado a Google News.

NOTA: SECOP vive en ingestion_clientes (detecta clientes potenciales, no regulación).

Container Bronze: bronze-regulatorio

Frecuencia:
  Mensual    -> regulación + inversión + noticias (cambian periódicamente)
  Trimestral -> financiamiento (convocatorias, cambian lentamente)
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
app = func.Blueprint()
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


# ═════════════════════════════════════════════════════════════
# BLOQUE REGULATORIO
# ═════════════════════════════════════════════════════════════

def ingest_mintic() -> dict:
    """
    QUÉ HACE:  portal de MinTIC (inicio, noticias, sala de prensa).
    PARA QUÉ:  KIN 2 — políticas públicas y programas del gobierno para el sector.
    Frecuencia: mensual.
    """
    urls = [
        "https://mintic.gov.co/portal/inicio/",
        "https://mintic.gov.co/portal/inicio/Noticias/",
        "https://mintic.gov.co/portal/inicio/Sala-de-Prensa/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "mintic",
        empresa    = "mintic",
        items_raw  = items_raw,
        source_url = "https://mintic.gov.co",
    )


def ingest_normativa_tic() -> dict:
    """
    QUÉ HACE:  normograma de MinTIC — compilación normativa del sector TIC
               (decretos, boletines jurídicos).
    PARA QUÉ:  KIN 1 — regulaciones que pueden impactar la operación de TAK.

    Reemplaza a la antigua normativa_legal: funcionpublica y suin-juriscol no
    servían; normograma es el portal jurídico REAL y estructurado del sector TIC.
    Frecuencia: mensual.
    """
    urls = [
        "https://normograma.mintic.gov.co/mintic/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "normativa_tic",
        empresa    = "normativa_tic",
        items_raw  = items_raw,
        source_url = "https://normograma.mintic.gov.co",
    )


def ingest_sic_datos() -> dict:
    """
    QUÉ HACE:  Superintendencia de Industria y Comercio — protección de datos
               personales (boletín jurídico + noticias).
    PARA QUÉ:  KIN 1 — la SIC es la autoridad de datos/privacidad en Colombia
               (Ley 1581 de 2012 / Hábeas Data). Vigila sanciones, circulares y
               el proyecto de ley que actualiza el régimen.

    Fuente añadida en la auditoría: antes no existía y es central para el KIN 1.
    Frecuencia: mensual.
    """
    urls = [
        "https://sedeelectronica.sic.gov.co/publicaciones/boletin-juridico/tema/Protecci%C3%B3n%20de%20Datos%20Personales",
        "https://www.sic.gov.co/noticias",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "sic_datos",
        empresa    = "SIC",
        items_raw  = items_raw,
        source_url = "https://www.sic.gov.co",
    )


def ingest_colombia_compra() -> dict:
    """
    QUÉ HACE:  Colombia Compra Eficiente — regulación de compra pública.
    PARA QUÉ:  KIN 1/2 — TAK está en el Acuerdo Marco de Nube Pública; los cambios
               de reglas aquí impactan directo su operación con el Estado.
    Frecuencia: mensual.
    """
    urls = [
        "https://www.colombiacompra.gov.co/",
        "https://www.colombiacompra.gov.co/noticias",
        "https://www.colombiacompra.gov.co/normativa",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "colombia_compra",
        empresa    = "colombia_compra_eficiente",
        items_raw  = items_raw,
        source_url = "https://www.colombiacompra.gov.co",
    )


# ═════════════════════════════════════════════════════════════
# BLOQUE FINANCIERO
# ═════════════════════════════════════════════════════════════

def ingest_financiamiento_tech() -> dict:
    """
    QUÉ HACE:  convocatorias, productos de crédito y noticias de las entidades que
               financian empresas/proyectos TI en Colombia.
    PARA QUÉ:  KIN 4 (oportunidades: convocatorias iNNpulsa/Minciencias/MinTIC +
               portafolio de crédito Bancóldex) y KIN 5 (condiciones de acceso:
               noticias de iNNpulsa y Bancóldex sobre nuevas líneas/tasas).
    Frecuencia: trimestral.

    NOTA: iNNpulsa y Bancóldex se reincorporaron con sus URLs REALES verificadas
    (las rutas anteriores estaban mal, no las entidades).
    """
    urls = [
        # Convocatorias (KIN 4)
        "https://convocatorias.innpulsacolombia.com/",
        "https://minciencias.gov.co/convocatorias",
        "https://www.mintic.gov.co/portal/inicio/Convocatorias/",
        # Productos de crédito (KIN 4)
        "https://www.bancoldex.com/portafolio-de-productos",
        # Noticias / condiciones de acceso (KIN 5)
        "https://www.innpulsacolombia.com/noticias/",
        "https://www.bancoldex.com/sobre-bancoldex-0/noticias-1",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "financiamiento_tech",
        empresa    = "fuentes_financiamiento",
        items_raw  = items_raw,
        source_url = "innpulsa_minciencias_mintic_bancoldex",
    )


def ingest_inversion_tech() -> dict:
    """
    QUÉ HACE:  ecosistema de inversión/VC en tech Colombia/LatAm.
    PARA QUÉ:  KIN 3/6 — tendencias de inversión e inversión de startups/competidores.
    Frecuencia: mensual.

    NOTA: se quitaron idbinvest, bancoldex/publicaciones e innpulsa/publicaciones
    (no cargaban, verificado). Quedan LatamList, Contxto y LAVCA.
    """
    urls = [
        "https://latamlist.com/colombia/",
        "https://www.contxto.com/es/colombia/",
        "https://lavca.org/industry-data/",
        "https://lavca.org/research/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "inversion_tech",
        empresa    = "ecosistema_inversion",
        items_raw  = items_raw,
        source_url = "latamlist_contxto_lavca",
    )


def ingest_noticias_regulatorio() -> dict:
    """
    QUÉ HACE:  noticias de regulación e inversión tech vía Google News (por tema),
               reutilizando news_client.buscar_noticias_empresa.
    PARA QUÉ:  señal fresca transversal a los KIN 1..6. Reemplaza los RSS
               hardcodeados (Portafolio/La República/Dinero), varios muertos.
    Frecuencia: mensual.
    """
    temas = [
        "regulacion datos privacidad Colombia",
        "politica publica tecnologia Colombia",
        "inversion venture capital Colombia tecnologia",
        "financiamiento startups tecnologia Colombia",
    ]
    todos_items = []
    for tema in temas:
        todos_items.extend(news.buscar_noticias_empresa(tema, limite=10))
        news.throttle()

    return _ingestar(
        fuente     = "noticias_regulatorio",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "google_news:regulacion_inversion_colombia",
    )


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "mintic":                ingest_mintic,
    "normativa_tic":         ingest_normativa_tic,
    "sic_datos":             ingest_sic_datos,
    "colombia_compra":       ingest_colombia_compra,
    "financiamiento_tech":   ingest_financiamiento_tech,
    "inversion_tech":        ingest_inversion_tech,
    "noticias_regulatorio":  ingest_noticias_regulatorio,
}

# Del timer mensual (financiamiento va en el trimestral)
FUENTES_MENSUAL = [
    "mintic", "normativa_tic", "sic_datos", "colombia_compra",
    "inversion_tech", "noticias_regulatorio",
]


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
    """Regulación + inversión + noticias — día 1 de cada mes 9am Colombia."""
    _ensure_tables()
    resumen = {}
    for nombre_f in FUENTES_MENSUAL:
        resumen[nombre_f] = FUENTES[nombre_f]()
        time.sleep(2)
    log.info("Regulatorio mensual: %s", resumen)


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
def ejecutar_regulatorio(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/regulatorio/ejecutar?fuente=sic_datos
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
def status_regulatorio(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/regulatorio/status
    GET /api/regulatorio/status?fuente=sic_datos
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
def reset_regulatorio(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/regulatorio/reset_cursor?fuente=sic_datos&empresa=SIC"""
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