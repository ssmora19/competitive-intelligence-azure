"""
ingestion_capacidades/function_capacidades.py
=============================================
Azure Function — KIT 7: Capacidades internas y talento humano

Fuentes automatizables Bronze:
  1. LinkedIn Jobs — qué perfiles TI demanda el mercado en Colombia
     → Detecta gaps entre lo que TAK tiene y lo que el mercado pide
  2. LinkedIn empleados TAK — perfil público del equipo de TAK
     → Qué certificaciones y skills tiene el equipo actual
  3. Portales de empleo Colombia — TI, Computrabajo, LinkedIn
     → Confirma demanda de perfiles específicos
  4. Web TAK — portafolio y servicios publicados
     → Qué capacidades declara TAK públicamente
  5. Certificaciones de fabricantes — Oracle, Microsoft, Red Hat
     → Qué niveles de certificación están disponibles

Fuentes NO automatizables (requieren datos internos de TAK):
  - Evaluaciones internas de desempeño
  - Auditorías tecnológicas internas
  - Cultura organizacional
  - Datos de RRHH

Container Bronze: bronze-capacidades
Frecuencia: trimestral — las capacidades cambian lentamente
"""

import os
import sys
import json
import time
import logging

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.apify_client     import ApifyClient, ACTOR_X
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
app   = func.FunctionApp()
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

def ingest_demanda_perfiles_ti() -> dict:
    """
    LinkedIn Jobs — perfiles TI demandados en Colombia.

    Compara qué pide el mercado vs qué tiene TAK.
    Si el mercado busca "Oracle Cloud Architect" y TAK no tiene ese perfil
    → gap de capacidad detectado automáticamente en Silver.

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
            {
                "keywords": keyword,
                "location": "Colombia",
                "limit":    5,
            }
        )
        for item in items:
            item["_keyword_buscada"] = keyword
        todos_items.extend(items)
        apify.throttle()

    return _ingestar(
        fuente     = "linkedin_jobs_perfiles",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "https://www.linkedin.com/jobs/",
    )


def ingest_web_tak() -> dict:
    """
    Web pública de TAK — capacidades y servicios declarados.

    Detecta qué está publicando TAK sobre sí mismo:
    servicios, tecnologías, casos de éxito, equipo.
    Sirve para comparar capacidades declaradas vs demanda del mercado.

    Frecuencia: trimestral.
    """
    urls = [
        "https://takcolombia.com.co/",
        "https://takcolombia.com.co/servicios/",
        "https://takcolombia.com.co/nosotros/",
        "https://takcolombia.com.co/soluciones/",
        "https://www.linkedin.com/company/tech-knowledge-tak/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "web_tak",
        empresa    = "TAK",
        items_raw  = items_raw,
        source_url = "https://tak.com.co",
    )


def ingest_certificaciones_fabricantes() -> dict:
    """
    Programas de certificación de los fabricantes partners de TAK.

    Detecta qué certificaciones están disponibles en Oracle, Microsoft,
    Red Hat e IBM — y cuáles son las más demandadas en el mercado.

    Frecuencia: trimestral.
    """
    urls = [
        # Oracle Certification
        "https://education.oracle.com/certification",
        "https://education.oracle.com/es/oracle-certification-program",

        # Red Hat Certification
        "https://www.redhat.com/es/services/certification",

        # Microsoft Certification
        "https://learn.microsoft.com/es-co/certifications/",

        # IBM Certification
        "https://www.ibm.com/training/credentials",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "certificaciones_fabricantes",
        empresa    = "fabricantes_tak",
        items_raw  = items_raw,
        source_url = "oracle_redhat_microsoft_ibm_certifications",
    )


def ingest_portales_empleo_ti() -> dict:
    """
    Portales de empleo Colombia — confirma demanda de perfiles TI.

    Elempleo y Computrabajo son los portales más usados en Colombia.
    Busca ofertas de trabajo con tecnologías del portafolio de TAK.

    Frecuencia: trimestral.
    """
    urls = [
        "https://www.elempleo.com/co/ofertas-empleo/oracle",
        "https://www.elempleo.com/co/ofertas-empleo/snowflake",
        "https://www.computrabajo.com.co/ofertas-de-trabajo/oracle",
        "https://www.computrabajo.com.co/ofertas-de-trabajo/infraestructura-ti",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "portales_empleo_ti",
        empresa    = "mercado_colombia",
        items_raw  = items_raw,
        source_url = "elempleo_computrabajo_colombia",
    )


def ingest_benchmarking_capacidades() -> dict:
    """
    Benchmarking de capacidades — qué ofrecen los competidores de TAK.

    Compara las capacidades publicadas de TAK vs competidores directos
    como SETI, Comware, CETUS, etc. — empresas colombianas similares.

    Frecuencia: trimestral.
    """
    urls = [
        "https://seti.com.co/servicios/",
        "https://www.comware.com.co/servicios/",
        "https://cetus.com.co/servicios/",
        "https://iteria.com.co/servicios/",
        "https://www.bmind.com/servicios/",
    ]
    items_raw = fc.scrape_many(urls)
    return _ingestar(
        fuente     = "benchmarking_capacidades",
        empresa    = "competidores_locales",
        items_raw  = items_raw,
        source_url = "seti_comware_cetus_iteria_bmind",
    )


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "demanda_perfiles_ti":       ingest_demanda_perfiles_ti,
    "web_tak":                   ingest_web_tak,
    "certificaciones_fabricantes": ingest_certificaciones_fabricantes,
    "portales_empleo_ti":        ingest_portales_empleo_ti,
    "benchmarking_capacidades":  ingest_benchmarking_capacidades,
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
    # LinkedIn Jobs usa Apify — se ejecuta siempre
    r1 = ingest_demanda_perfiles_ti()
    r2 = ingest_web_tak()
    r3 = ingest_certificaciones_fabricantes()
    r4 = ingest_portales_empleo_ti()
    r5 = ingest_benchmarking_capacidades()
    log.info("Capacidades trimestral: perfiles=%s tak=%s cert=%s empleo=%s bench=%s",
             r1, r2, r3, r4, r5)


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS
# ─────────────────────────────────────────────────────────────

@app.route(route="capacidades/ejecutar", methods=["GET", "POST"])
def ejecutar(req: func.HttpRequest) -> func.HttpResponse:
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
def status(req: func.HttpRequest) -> func.HttpResponse:
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
def reset(req: func.HttpRequest) -> func.HttpResponse:
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
