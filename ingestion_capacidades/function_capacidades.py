"""
ingestion_capacidades/function_capacidades.py
=============================================
Azure Function — KIT 7: Capacidades internas y talento humano

KIN que responde:
  1. ¿Nivel del talento técnico de TAK frente a las exigencias del mercado?
        -> interno (web/LinkedIn TAK) + mercado (LinkedIn Jobs). Gap = Gold.
  2. ¿Qué tan preparada está TAK para adoptar nuevas tecnologías?
        -> web TAK (tecnologías/partners) + certificaciones disponibles (referencia).
  3. ¿Qué capacidades de innovación tiene TAK?
        -> web TAK (servicios, casos, industrias).
  4. ¿Qué tan rápida es la adaptación al cambio?
        -> INTERNO/CUALITATIVO (cultura, RRHH, desempeño): NO automatizable por web.

Fuentes Bronze:
  1. web_tak                     -> lo que TAK declara de sí mismo   (KIN 1,2,3)
  2. demanda_perfiles_ti         -> lo que el mercado pide (baseline) (KIN 1)
  3. certificaciones_fabricantes -> certificaciones disponibles (ref) (KIN 2)

Fuentes NO automatizables (requieren datos internos de TAK — no van en Bronze):
  - Evaluaciones de desempeño, auditorías internas, cultura, RRHH  (KIN 4).
  - Certificaciones que YA tiene el equipo de TAK: vendrían de los perfiles
    LinkedIn de empleados, pero scrapear perfiles personales es sensible por
    privacidad -> se deja fuera; se puede levantar como dato interno manual.

Cambios de la auditoría (enfoque + bugs):
  - Eliminado benchmarking_capacidades: era data de COMPETIDORES (su lugar es
    el KIT de competidores, donde SETI/Cetus/Iteria/Bmind ya están en
    companies.json). Además incluía a Comware, que es CLIENTE (clientes.json).
  - Eliminado portales_empleo_ti: redundante con LinkedIn Jobs (misma señal de
    demanda de mercado) y con URLs de formato dudoso.
  - Eliminado el import muerto ACTOR_X (no se usaba).
  - Corregido source_url de web_tak (antes decía tak.com.co; scrapea takcolombia.com.co).
  - Docstrings reescritos (QUÉ HACE / PARA QUÉ): el viejo decía que benchmarking
    "compara" capacidades, pero Bronze no compara — eso es Gold.

Container Bronze: bronze-capacidades
Frecuencia: trimestral — las capacidades cambian lentamente.
"""

import os
import sys
import json
import time
import logging

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
BRONZE_CONTAINER = "bronze-capacidades"

log   = logging.getLogger("ci.capacidades")
app = func.Blueprint()
fc    = FirecrawlClient()
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
            variable_ic = "capacidades",
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

def ingest_web_tak() -> dict:
    """
    QUÉ HACE:  scrapea la web pública de TAK (portafolio, servicios, industrias,
               compañía, recursos, soporte) y su página de LinkedIn.
    PARA QUÉ:  KIN 1/2/3 — capacidades que TAK DECLARA de sí mismo: servicios,
               tecnologías/partners (preparación) y casos/industrias (innovación).

    NOTA: LinkedIn bloquea scraping; puede devolver poco o un muro de login. El
    filtro de statusCode del firecrawl_client descarta lo que no sea 200.

    Frecuencia: trimestral.
    """
    urls = [
        "https://takcolombia.com.co/",
        "https://takcolombia.com.co/servicios.html",
        "https://takcolombia.com.co/industrias.html",
        "https://takcolombia.com.co/compania.html",
        "https://takcolombia.com.co/recursos.html",
        "https://takcolombia.com.co/soporte.html",
        "https://www.linkedin.com/company/tak-colombia/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "web_tak",
        empresa    = "TAK",
        items_raw  = items_raw,
        source_url = "https://takcolombia.com.co/",
    )


def ingest_demanda_perfiles_ti() -> dict:
    """
    QUÉ HACE:  busca en LinkedIn Jobs (Apify) los perfiles TI que demanda el
               mercado colombiano en las tecnologías del portafolio de TAK.
    PARA QUÉ:  KIN 1 — es la BASELINE de mercado contra la cual Gold comparará
               las capacidades internas de TAK (el "gap"). NO son capacidades de
               TAK: es lo que el mercado pide. Por eso empresa = mercado_colombia.

    Frecuencia: trimestral.
    """
    keywords = [
        "Oracle DBA Colombia",
        "Snowflake Engineer Colombia",
        "Oracle Cloud Colombia",
        "Red Hat Colombia",
        "Cloud Architect Colombia",
        "Data Engineer Oracle Colombia",
        "Ciberseguridad Colombia",
        "Infraestructura TI Colombia",
    ]

    todos_items = []
    for keyword in keywords:
        items = apify.run_actor(
            "valig~linkedin-jobs-scraper",
            {"keywords": keyword, "location": "Colombia", "limit": 5},
        )
        for item in items:
            item["_keyword_buscada"] = keyword
        todos_items.extend(items)
        apify.throttle()

    return _ingestar(
        fuente     = "demanda_perfiles_ti",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "https://www.linkedin.com/jobs/",
    )


def ingest_certificaciones_fabricantes() -> dict:
    """
    QUÉ HACE:  scrapea los programas de certificación de los fabricantes partners
               de TAK (Oracle, Red Hat, Microsoft, IBM).
    PARA QUÉ:  KIN 2 (referencia) — el universo de certificaciones DISPONIBLES en
               el stack de TAK, contra el cual medir su preparación. Es contexto,
               NO las certificaciones que el equipo de TAK ya tiene (eso sería
               dato interno / perfiles de empleados, fuera de Bronze).

    Frecuencia: trimestral.
    """
    urls = [
        "https://education.oracle.com/certification",
        "https://education.oracle.com/es/oracle-certification-program",
        "https://www.redhat.com/es/services/certification",
        "https://learn.microsoft.com/es-co/certifications/",
        "https://www.ibm.com/training/credentials",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "certificaciones_fabricantes",
        empresa    = "fabricantes_tak",
        items_raw  = items_raw,
        source_url = "oracle_redhat_microsoft_ibm_certifications",
    )


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "web_tak":                     ingest_web_tak,
    "demanda_perfiles_ti":         ingest_demanda_perfiles_ti,
    "certificaciones_fabricantes": ingest_certificaciones_fabricantes,
}


def _ensure_tables():
    try:
        init_delta_table(CONN_STR)
    except Exception as ex:
        log.warning("Tablas de control: %s", ex)


# ─────────────────────────────────────────────────────────────
# TRIGGERS — Timer (trimestral)
# ─────────────────────────────────────────────────────────────

@app.timer_trigger(schedule="0 0 14 1 1,4,7,10 *", arg_name="timer", run_on_startup=False)
def timer_capacidades_trimestral(timer: func.TimerRequest) -> None:
    """
    Todas las fuentes — trimestral (1 ene, 1 abr, 1 jul, 1 oct) 9am Colombia.
    Las capacidades cambian lentamente — trimestral es suficiente.
    """
    _ensure_tables()
    r1 = ingest_web_tak()
    r2 = ingest_demanda_perfiles_ti()
    r3 = ingest_certificaciones_fabricantes()
    log.info("Capacidades trimestral: tak=%s demanda=%s cert=%s", r1, r2, r3)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS
# ─────────────────────────────────────────────────────────────

@app.route(route="capacidades/ejecutar", methods=["GET", "POST"])
def ejecutar_capacidades(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/capacidades/ejecutar?fuente=web_tak
    GET /api/capacidades/ejecutar?fuente=todas
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
        log.info("Manual capacidades: ejecutando %s", nombre_f)
        res = fn()
        resultados.append(res)
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="capacidades/status", methods=["GET"])
def status_capacidades(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/capacidades/status
    GET /api/capacidades/status?fuente=web_tak
    GET /api/capacidades/status?modo=blobs
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


@app.route(route="capacidades/reset_cursor", methods=["POST"])
def reset_capacidades(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/capacidades/reset_cursor?fuente=web_tak&empresa=TAK"""
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