"""
ingestion_clientes/function_clientes.py
=======================================
Azure Function — KIT 2: Comportamiento y necesidades de clientes

Este módulo tiene DOS partes claramente separadas:

  PARTE A — CLIENTES CONOCIDOS (los 19 que TAK entregó)
    Lee la lista de clientes.json y vigila a cada uno con sus fuentes REALES:
      · Públicos    -> SECOP por nombre de ENTIDAD compradora (+ sombrilla) + Google News
      · TI privados -> SECOP como ADJUDICATARIO (proveedor) + Google News
      · Privados    -> Google News (+ web de sala de prensa si existe)
    Las noticias van por Google News (nombre), NO scrapeando rutas web (evita 404).

  PARTE B — CLIENTES POTENCIALES (escaneo de mercado)
    · SECOP licitaciones TI abiertas -> el 95-97% de oportunidades que TAK no ve
    · LinkedIn Jobs Colombia         -> qué tecnologías demandan las empresas

Container Bronze: bronze-clientes

Cambios respecto a la versión anterior (bugs eliminados):
  - Eliminado ingest_redes_sociales_clientes (usaba ACTOR_X, import muerto -> crash).
  - Eliminado ingest_camara_comercio, ingest_comunidades_tech,
    ingest_reportes_sectoriales_clientes (rutas Firecrawl adivinadas -> 404,
    y además eran señal de MERCADO, no de clientes).
  - Añadida la vigilancia real de los 19 clientes conocidos (Parte A).
  - Web (solo privados con sala de prensa) filtrada por statusCode 200.

Dependencias nuevas:
  - shared/news_client.py  -> método buscar_noticias_empresa (Google News)
  - shared/secop_client.py -> métodos get_procesos_por_entidad /
                              get_contratos_por_entidad (ver secop_client_ADICION.py)
"""

import os
import sys
import json
import time
import logging
from typing import Optional

import azure.functions as func

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.secop_client     import SecopClient
from shared.apify_client     import ApifyClient
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
BRONZE_CONTAINER = "bronze-clientes"
CLIENTES_JSON    = os.path.join(os.path.dirname(__file__), "clientes.json")

log       = logging.getLogger("ci.clientes")
app = func.Blueprint()
secop     = SecopClient()
apify     = ApifyClient()
firecrawl = FirecrawlClient()
news      = NewsClient()


# ─────────────────────────────────────────────────────────────
# CARGA DEL CATÁLOGO DE CLIENTES (clientes.json)
# ─────────────────────────────────────────────────────────────
def get_clientes(solo_activos: bool = True) -> list[dict]:
    """
    QUÉ HACE:  lee clientes.json y devuelve la lista de clientes.
    PARA QUÉ:  desacoplar la LISTA (dato editable) del CÓDIGO (proceso),
               igual que companies.json en competidores.

    Nunca lanza: si el archivo falta o está corrupto, devuelve [] y loguea.
    """
    try:
        with open(CLIENTES_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        log.error("clientes.json no encontrado en %s", CLIENTES_JSON)
        return []
    except json.JSONDecodeError as ex:
        log.error("clientes.json malformado: %s", ex)
        return []

    if solo_activos:
        data = [c for c in data if c.get("activo", True)]
    return data


# ─────────────────────────────────────────────────────────────
# ORQUESTADOR DELTA — idéntico al de los demás módulos
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
            variable_ic = "clientes",
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
        blob_paths    = blob_paths,
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
# Utilidad: filtrar 404s del scraping web (statusCode 200)
# ─────────────────────────────────────────────────────────────
def _solo_status_ok(items: list[dict]) -> list[dict]:
    """
    Descarta páginas que no devolvieron HTTP 200 (evita guardar 404 como blob).
    Es defensivo: si el item no trae statusCode, se asume válido para no
    perder datos buenos. El filtro de raíz vive en firecrawl_client.py.
    """
    ok = []
    for it in items or []:
        code = it.get("statusCode")
        if code is None:
            meta = it.get("metadata") or {}
            code = meta.get("statusCode")
        if code is None or int(code) == 200:
            ok.append(it)
        else:
            log.info("web cliente: descartado statusCode=%s (%s)",
                     code, it.get("url") or it.get("_url", ""))
    return ok


# ═════════════════════════════════════════════════════════════
# PARTE A — VIGILANCIA DE LOS 19 CLIENTES CONOCIDOS
# ═════════════════════════════════════════════════════════════

def _monitorear_cliente(cli: dict) -> list[dict]:
    """
    QUÉ HACE:  vigila UN cliente según su 'tipo', devolviendo un resultado
               por cada fuente que aplique.
    PARA QUÉ:  encapsular la lógica por cliente para que un fallo en uno no
               tumbe a los demás (cada llamada externa va en su try/except).

    Reglas por tipo:
      publico        -> SECOP procesos + contratos por ENTIDAD compradora
      ti_privado     -> SECOP contratos como ADJUDICATARIO (proveedor)
      privado/grande -> (sin SECOP)
      TODOS          -> Google News por nombre  (+ web si hay sala de prensa)
    """
    nombre   = cli.get("nombre", "desconocido")
    tipo     = cli.get("tipo", "privado")
    empresa  = nombre  # etiqueta de trazabilidad por cliente
    resultados = []

    # ---- 1) SECOP -------------------------------------------------
    secop_nombres = cli.get("secop_nombres") or []

    if tipo == "publico":
        # Entidad COMPRADORA -> procesos + contratos por nombre_entidad
        procesos, contratos = [], []
        for sn in secop_nombres:
            try:
                procesos += secop.get_procesos_por_entidad(sn, limite=100)
            except Exception as ex:
                log.warning("[%s] SECOP procesos falló (%s): %s", nombre, sn, ex)
            try:
                contratos += secop.get_contratos_por_entidad(sn, limite=100)
            except Exception as ex:
                log.warning("[%s] SECOP contratos falló (%s): %s", nombre, sn, ex)

        if procesos:
            resultados.append(_ingestar(
                fuente="secop_cliente_procesos", empresa=empresa,
                items_raw=procesos,
                source_url="https://www.datos.gov.co/resource/p6dx-8zbt.json",
            ))
        if contratos:
            resultados.append(_ingestar(
                fuente="secop_cliente_contratos", empresa=empresa,
                items_raw=contratos,
                source_url="https://www.datos.gov.co/resource/jbjy-vk9h.json",
            ))

    elif tipo == "ti_privado":
        # Empresa de TI: gana contratos -> se busca como ADJUDICATARIO
        adjudicados = []
        for sn in secop_nombres:
            try:
                adjudicados += secop.get_contratos_competidor(
                    nombre_empresa=sn, limite=50
                )
            except Exception as ex:
                log.warning("[%s] SECOP adjudicatario falló (%s): %s", nombre, sn, ex)
        if adjudicados:
            resultados.append(_ingestar(
                fuente="secop_cliente_adjudicatario", empresa=empresa,
                items_raw=adjudicados,
                source_url="https://www.datos.gov.co/resource/jbjy-vk9h.json",
            ))
    # privado / privado_grande -> no aplica SECOP como comprador

    # ---- 2) Noticias (Google News por nombre) — TODOS -------------
    query = cli.get("noticias_query") or nombre
    try:
        noticias = news.buscar_noticias_empresa(query, limite=10)
        if noticias:
            resultados.append(_ingestar(
                fuente="noticias_cliente", empresa=empresa,
                items_raw=noticias,
                source_url=f"google_news:{query}",
            ))
    except Exception as ex:
        log.warning("[%s] Google News falló: %s", nombre, ex)

    # ---- 3) Web / sala de prensa (solo si hay URL real) ----------
    web = cli.get("web")
    if web:
        try:
            crudo = firecrawl.scrape_many([web])
            limpio = _solo_status_ok(crudo)
            if limpio:
                resultados.append(_ingestar(
                    fuente="web_cliente", empresa=empresa,
                    items_raw=limpio, source_url=web,
                ))
        except Exception as ex:
            log.warning("[%s] Web scraping falló (%s): %s", nombre, web, ex)

    if not resultados:
        resultados.append({"empresa": empresa, "status": "sin_fuentes_activas",
                           "items_new": 0})
    return resultados


def ingest_clientes_conocidos(desde: int = 0, hasta: int | None = None) -> list[dict]:
    """
    QUÉ HACE:  recorre clientes.json y vigila a cada cliente activo.
    PARA QUÉ:  Parte A — inteligencia sobre los 19 clientes reales de TAK.

    PROCESAMIENTO EN TANDAS (desde/hasta): como cada cliente consulta SECOP
    (lento) + noticias, correr los 19 de una excede el límite de 10 min de
    Azure Functions. Por eso se puede procesar por rangos:
        ?fuente=clientes_conocidos&desde=0&hasta=5   (clientes 0..4)
        ?fuente=clientes_conocidos&desde=5&hasta=10  (clientes 5..9)
    Sin desde/hasta procesa todos (útil solo si son pocos).

    Cada cliente va aislado en su try/except: si uno falla, los demás siguen.
    """
    clientes = get_clientes(solo_activos=True)
    if not clientes:
        return [{"status": "sin_catalogo", "items_new": 0}]

    # recorte por tanda
    total = len(clientes)
    hasta = total if hasta is None else min(hasta, total)
    clientes = clientes[desde:hasta]
    log.info("clientes_conocidos: procesando %d..%d de %d", desde, hasta, total)

    todos = []
    for cli in clientes:
        nombre = cli.get("nombre", "desconocido")
        try:
            res = _monitorear_cliente(cli)
            todos.extend(res)
        except Exception as ex:
            log.error("[%s] fallo inesperado: %s", nombre, ex)
            todos.append({"empresa": nombre, "status": "error", "detalle": str(ex)})
        time.sleep(0.5)  # cortesía con las APIs
    return todos


# ═════════════════════════════════════════════════════════════
# PARTE B — CLIENTES POTENCIALES (escaneo de mercado)
# ═════════════════════════════════════════════════════════════

def ingest_secop_licitaciones() -> dict:
    """
    QUÉ HACE:  trae licitaciones TI abiertas hoy en SECOP II.
    PARA QUÉ:  detectar oportunidades ACTIVAS (el 95-97% que TAK no persigue).

    Frecuencia: diaria — una licitación puede abrirse y cerrarse en días.
    """
    items_raw = secop.get_procesos_activos_tak(limite=200)
    return _ingestar(
        fuente     = "secop_licitaciones",
        empresa    = "sector_publico",
        items_raw  = items_raw,
        source_url = "https://www.datos.gov.co/resource/p6dx-8zbt.json",
    )


def ingest_linkedin_jobs() -> dict:
    """
    QUÉ HACE:  busca ofertas de empleo TI en Colombia (LinkedIn Jobs vía Apify).
    PARA QUÉ:  si una empresa busca Oracle DBA / Snowflake, es cliente potencial.
    Actor: valig~linkedin-jobs-scraper (gratuito, sin cookies).
    """
    # Palabras clave derivadas del portafolio REAL de TAK (su web: servicios +
    # industrias). Empresas que contratan estos perfiles = clientes potenciales.
    #   - Núcleo Oracle (lo más distintivo de TAK): DBA, Exadata, ODI, BD Oracle
    #   - Servicios fuertes transversales: BI/Power BI, Machine Learning,
    #     ciberseguridad, infraestructura cloud
    keywords = [
        "Oracle DBA", "Exadata", "Oracle Data Integrator", "base de datos Oracle",
        "Business Intelligence", "Power BI", "Machine Learning",
        "ciberseguridad", "infraestructura cloud",
    ]
    todos_items = []
    for keyword in keywords:
        try:
            items = apify.run_actor(
                "valig~linkedin-jobs-scraper",
                {"keywords": keyword, "location": "Colombia", "limit": 10},
            )
            for item in items:
                item["_keyword_buscada"] = keyword
            todos_items.extend(items)
            apify.throttle()
        except Exception as ex:
            log.warning("LinkedIn Jobs falló para '%s': %s", keyword, ex)

    return _ingestar(
        fuente     = "linkedin_jobs",
        empresa    = "mercado_colombia",
        items_raw  = todos_items,
        source_url = "https://www.linkedin.com/jobs/",
    )


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES (para ejecución manual y timers)
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "clientes_conocidos": ingest_clientes_conocidos,   # Parte A (los 19)
    "secop_licitaciones": ingest_secop_licitaciones,   # Parte B
    "linkedin_jobs":      ingest_linkedin_jobs,         # Parte B
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
def timer_secop_licitaciones(timer: func.TimerRequest) -> None:
    """Parte B — SECOP licitaciones: diario 7:00am Colombia (12:00 UTC)."""
    _ensure_tables()
    log.info("Clientes SECOP licitaciones: %s", ingest_secop_licitaciones())


@app.timer_trigger(schedule="0 0 13 * * 1", arg_name="timer", run_on_startup=False)
def timer_clientes_conocidos(timer: func.TimerRequest) -> None:
    """Parte A — vigilancia de los 19 clientes: lunes 8:00am Colombia (13:00 UTC)."""
    _ensure_tables()
    log.info("Clientes conocidos: %s", ingest_clientes_conocidos())


# ── TIMER APIFY DESACTIVADO (evita gasto accidental de crédito) ──
# Esta fuente SOLO se ejecuta manualmente por URL.
# @app.timer_trigger(schedule="0 30 13 * * 1", arg_name="timer", run_on_startup=False)
# def timer_linkedin_jobs(timer: func.TimerRequest) -> None:
#     """Parte B — LinkedIn Jobs: lunes 8:30am Colombia (13:30 UTC)."""
#     _ensure_tables()
#     log.info("Clientes LinkedIn Jobs: %s", ingest_linkedin_jobs())


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS — ejecución manual y observabilidad
# ─────────────────────────────────────────────────────────────

@app.route(route="clientes/ejecutar", methods=["GET", "POST"])
def ejecutar_clientes(req: func.HttpRequest) -> func.HttpResponse:
    """
    Ejecuta una fuente manualmente.
      GET /api/clientes/ejecutar?fuente=clientes_conocidos
      GET /api/clientes/ejecutar?fuente=secop_licitaciones
      GET /api/clientes/ejecutar?fuente=todas
      GET /api/clientes/ejecutar?cliente=DANE   (vigila un solo cliente)
    """
    _ensure_tables()

    # Modo: un solo cliente por nombre
    cliente = req.params.get("cliente", "").strip()
    if cliente:
        match = [c for c in get_clientes(solo_activos=False)
                 if c.get("nombre", "").lower() == cliente.lower()]
        if not match:
            return func.HttpResponse(
                json.dumps({"error": f"Cliente '{cliente}' no está en clientes.json",
                            "clientes": [c["nombre"] for c in get_clientes(False)]},
                           ensure_ascii=False),
                status_code=404, mimetype="application/json",
            )
        res = _monitorear_cliente(match[0])
        return func.HttpResponse(
            json.dumps({"cliente": cliente, "resultados": res},
                       ensure_ascii=False, default=str),
            status_code=200, mimetype="application/json",
        )

    fuente = req.params.get("fuente", "").lower()
    if not fuente or (fuente not in FUENTES and fuente != "todas"):
        return func.HttpResponse(
            json.dumps({"error": "Parámetro 'fuente' o 'cliente' requerido.",
                        "opciones": list(FUENTES.keys()) + ["todas"]},
                       ensure_ascii=False),
            status_code=400, mimetype="application/json",
        )

    # parámetros de tanda (solo aplican a clientes_conocidos)
    def _int_param(nombre):
        v = req.params.get(nombre)
        return int(v) if v is not None and v.isdigit() else None
    desde = _int_param("desde") or 0
    hasta = _int_param("hasta")

    resultados = []
    targets = FUENTES.items() if fuente == "todas" else [(fuente, FUENTES[fuente])]
    for nombre_f, fn in targets:
        log.info("Manual clientes: ejecutando %s", nombre_f)
        if nombre_f == "clientes_conocidos":
            res = fn(desde=desde, hasta=hasta)
        else:
            res = fn()
        # unos ingestores devuelven dict, otros lista de dicts
        resultados.extend(res if isinstance(res, list) else [res])
        time.sleep(1)

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="clientes/status", methods=["GET"])
def status_clientes(req: func.HttpRequest) -> func.HttpResponse:
    """
    Observabilidad del KIT 2.
      GET /api/clientes/status
      GET /api/clientes/status?fuente=noticias_cliente
      GET /api/clientes/status?modo=blobs
    """
    fuente = req.params.get("fuente", "")
    modo   = req.params.get("modo", "cursores")
    limite = int(req.params.get("limite", "30"))
    try:
        if modo == "blobs":
            blobs = list_recent_blobs(
                CONN_STR, BRONZE_CONTAINER,
                prefix=f"{fuente}/" if fuente else "", limite=limite,
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


@app.route(route="clientes/reset_cursor", methods=["POST"])
def reset_clientes(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/clientes/reset_cursor?fuente=noticias_cliente&empresa=DANE
    """
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