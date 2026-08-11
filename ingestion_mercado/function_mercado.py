"""
ingestion_mercado/function_mercado.py
=====================================
Azure Function — KIT 5: Comportamiento del mercado tecnológico

KIN que responde:
  1. ¿Qué segmentos del mercado presentan mayor crecimiento en la demanda?
  2. ¿Qué necesidades no satisfechas existen en el mercado?
  3. ¿Qué regiones o sectores presentan oportunidades de expansión para TAK?

Fuentes Bronze (auditadas contra los KIN — solo fuentes REALES y verificadas):
  1. DANE — Indicadores básicos de TIC en Empresas (Firecrawl)   -> KIN 1, 3
  2. Fedesoft + Cenisoft — estudios del sector software/TI (Firecrawl) -> KIN 1, 2
  3. Noticias de mercado — Google News por tema (news_client)    -> KIN 1, 2
  4. Redes sociales de mercado — X/Twitter (Apify) [OPCIONAL]     -> señal blanda

Nota KIN 3 (regiones/sectores con oportunidad de expansión):
  NO se ingiere aquí. La mejor respuesta es el SECOP que ya se ingiere en
  clientes/competidores, agregado por departamento y sector en la capa Gold.
  Duplicar esa ingesta aquí ensuciaría Bronze sin aportar señal nueva.

Fuentes ELIMINADAS en la auditoría (no eran válidas):
  - IDC / Gartner / Everest: informes de pago, no scrapeables (daban 404/marketing).
  - BanRep /economia-digital, DNP /desarrollo-empresarial/tic, Colombia
    Productiva /software-y-ti: rutas supuestas que no existían (404).
  - colombiatic stats.php y sala de prensa MinTIC genérica: stale / 404.
  - RSS hardcodeados (Dinero, etc.): varios feeds muertos -> migrado a Google News.

Container Bronze: bronze-mercado

Frecuencia:
  Trimestral -> DANE + estudios sectoriales (reportes que casi no cambian)
  Mensual    -> noticias (Google News) + redes (si se activa)
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
app = func.Blueprint()
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
    QUÉ HACE:
      Scrapea la operación del DANE "Indicadores básicos de TIC en Empresas"
      (página actual + históricos), que trae adopción de tecnología en
      empresas colombianas por actividad económica.
    PARA QUÉ SIRVE:
      KIN 1 (segmentos con mayor adopción/demanda) y KIN 3 (por sector).

    NOTA: el DANE publica los datos en boletines PDF enlazados desde estas
    páginas; el scraping trae la página índice con los enlaces. El filtro de
    statusCode del firecrawl_client descarta cualquier ruta que dé 404.

    Frecuencia: trimestral (el DANE actualiza esta operación 1-2 veces al año).
    """
    urls = [
        # Verificadas reales:
        "https://www.dane.gov.co/index.php/estadisticas-por-tema/tecnologia-e-innovacion/tecnologias-de-la-informacion-y-las-comunicaciones-tic/indicadores-basicos-de-tic-en-empresas",
        "https://www.dane.gov.co/index.php/estadisticas-por-tema/tecnologia-e-innovacion/tecnologias-de-la-informacion-y-las-comunicaciones-tic/indicadores-basicos-de-tic-en-empresas/indicadores-basicos-de-tic-en-empresas-historicos",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "dane_estadisticas",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "dane_tic_empresas",
    )


def ingest_reportes_economicos() -> dict:
    """
    QUÉ HACE:
      Scrapea las publicaciones del gremio del software colombiano: Fedesoft
      (noticias/comunicados del sector) y Cenisoft (estudios de empleabilidad
      y talento TI).
    PARA QUÉ SIRVE:
      KIN 1 (crecimiento del sector software/TI) y KIN 2 (necesidades no
      satisfechas: brecha de talento, habilidades más demandadas).

    Frecuencia: trimestral (publican por congreso/estudio, no a diario).
    """
    urls = [
        # Verificadas reales:
        "https://fedesoft.org/noticias/",
        "https://cenisoft.org/estudioempleabilidadti/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "reportes_economicos",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "fedesoft_cenisoft",
    )


def ingest_ontic_barreras_adopcion() -> dict:
    """
    QUÉ HACE:
      Scrapea indicadores del Observatorio Nacional de TIC (ONTIC) de MinTIC,
      que procesa las encuestas del DANE: razones de NO adopción de TIC y
      adopción por sector económico.
    PARA QUÉ SIRVE:
      - Mercado KIN 2 (necesidades no satisfechas del mercado).
      - CRUZADO → Clientes KIN 7 (barreras que impiden la adopción) y
        Clientes KIN 8 (madurez digital de segmentos que no adoptan).
      Se ingiere aquí, en mercado (señal de nivel-mercado), NO en clientes, para
      no duplicar. Gold lo cruza con los KIN de clientes.

    NOTA: indicadores del catálogo de ONTIC referidos a EMPRESAS (fuente:
    Encuesta TIC en Empresas del DANE), desagregables por sector/tamaño = madurez
    por segmento. Se pueden agregar más indicadores del catálogo si aplican.

    Frecuencia: trimestral.
    """
    urls = [
        "https://ontic.mintic.gov.co/portal/Secciones/Indicadores/",
        # Empresas — madurez / adopción por segmento (KIN 8) :
        "https://ontic.mintic.gov.co/portal/Secciones/Indicadores/Transformacion-digital-productiva/383060:Empresas-que-usaron-Internet-y-herramientas-tecnologicas",
        "https://ontic.mintic.gov.co/portal/Secciones/Indicadores/Transformacion-digital-productiva/399417:Empresas-innovadoras-del-sector-servicios-y-comercio",
        "https://ontic.mintic.gov.co/portal/Secciones/Indicadores/Transformacion-digital-productiva/399418:Empresas-del-sector-servicios-y-comercio-que-usan-tableros-de-control-o-seguimiento",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "ontic_barreras_adopcion",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "ontic_mintic_indicadores",
    )


def ingest_noticias_economicas() -> dict:
    """
    QUÉ HACE:
      Trae noticias de mercado tecnológico colombiano vía Google News RSS
      (por tema), reutilizando news_client.buscar_noticias_empresa.
    PARA QUÉ SIRVE:
      KIN 1 y 2 — señal fresca de crecimiento, inversión y necesidades del
      mercado. Reemplaza los RSS hardcodeados (varios muertos) por Google
      News, que no requiere adivinar rutas ni mantener feeds.

    Frecuencia: mensual.
    """
    temas = [
        "mercado tecnologia Colombia",
        "transformacion digital empresas Colombia",
        "inversion tecnologia Colombia",
        "software TI Colombia crecimiento",
        "adopcion nube empresas Colombia",
    ]

    todos_items = []
    for tema in temas:
        noticias = news.buscar_noticias_empresa(tema, limite=10)
        todos_items.extend(noticias)
        news.throttle()

    return _ingestar(
        fuente     = "noticias_economicas",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "google_news:mercado_ti_colombia",
    )


def ingest_redes_sociales_mercado() -> dict:
    """
    QUÉ HACE:
      X/Twitter — conversaciones sobre mercado TI Colombia por hashtags.
    PARA QUÉ SIRVE:
      Señal BLANDA de necesidades/tendencias mencionadas en redes.

    ⚠️ OPCIONAL — evaluar contra los KIN antes de dejarla activa:
      · Consume crédito de Apify.
      · El ruido de hashtags aporta poco a KIN de mercado frente a DANE/Fedesoft.
      Por eso NO está en el timer por defecto; queda disponible por HTTP
      (?fuente=redes_sociales_mercado) para que decidas si la conservas.

    Frecuencia: mensual (si se activa).
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
    "ontic_barreras_adopcion": ingest_ontic_barreras_adopcion,
    "noticias_economicas":     ingest_noticias_economicas,
    "redes_sociales_mercado":  ingest_redes_sociales_mercado,  # opcional (HTTP)
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
    DANE + reportes sectoriales — trimestral (1 ene, abr, jul, oct) 9am Colombia.
    """
    _ensure_tables()
    r1 = ingest_dane_estadisticas()
    r2 = ingest_reportes_economicos()
    r3 = ingest_ontic_barreras_adopcion()
    log.info("Mercado trimestral: dane=%s reportes=%s ontic=%s", r1, r2, r3)


@app.timer_trigger(schedule="0 0 14 1 * *", arg_name="timer", run_on_startup=False)
def timer_mercado_mensual(timer: func.TimerRequest) -> None:
    """
    Noticias de mercado (Google News) — mensual día 1 9am Colombia.
    (Redes sociales queda fuera del timer por defecto; ver docstring.)
    """
    _ensure_tables()
    r1 = ingest_noticias_economicas()
    log.info("Mercado mensual: noticias=%s", r1)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS
# ─────────────────────────────────────────────────────────────

@app.route(route="mercado/ejecutar", methods=["GET", "POST"])
def ejecutar_mercado(req: func.HttpRequest) -> func.HttpResponse:
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
def status_mercado(req: func.HttpRequest) -> func.HttpResponse:
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
def reset_mercado(req: func.HttpRequest) -> func.HttpResponse:
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