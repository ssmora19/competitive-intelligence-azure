"""
ingestion_competidores/function_competidores.py
================================================
Azure Function — dominio COMPETIDORES

Responsabilidad:
  Orquestar la ingestión Bronze de todas las fuentes del dominio
  competidores: Instagram, Facebook, TikTok, LinkedIn, X, YouTube y Web.

Esta función NO:
  - hace requests HTTP directamente (usa ApifyClient / YouTubeClient)
  - sabe cómo guardar en Blob (usa storage.save_bronze)
  - sabe qué es nuevo o repetido (usa state_manager.apply_delta)
  - escribe logs (usa logger.IngestLogger)

Solo orquesta: obtiene datos → aplica delta → guarda nuevos → registra resultado.

Triggers:
  Timer diario   → redes sociales de alta volatilidad (6am–7am Colombia)
  Timer semanal  → web scraping (domingos 2am)
  HTTP manual    → ejecución desde Azure Portal o Postman

Endpoints de observabilidad:
  GET /api/competidores/status
  GET /api/competidores/status?fuente=instagram
  GET /api/competidores/status?modo=blobs
  POST /api/competidores/reset_cursor?fuente=instagram&empresa=Accenture
"""

import os
import sys
import json
import time
import logging
from typing import Optional

import azure.functions as func

# Agregar el directorio raíz al path para importar shared
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.apify_client import (
    ApifyClient,
    build_instagram_payload, build_facebook_payload, build_tiktok_payload,
    build_linkedin_payload,  build_x_payload,
    ACTOR_INSTAGRAM, ACTOR_FACEBOOK, ACTOR_TIKTOK,
    ACTOR_LINKEDIN,  ACTOR_X,
)
from shared.youtube_client   import YouTubeClient
from shared.firecrawl_client import FirecrawlClient, build_company_urls
from shared.storage         import save_bronze, list_recent_blobs
from shared.state_manager   import (
    apply_delta, get_cursor, update_cursor, reset_cursor,
    extract_item_id, get_all_cursors, init_delta_table,
)
from shared.logger          import IngestLogger, get_last_runs

# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
CONN_STR         = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
BRONZE_CONTAINER = "bronze-competidores"
DATA_PATH        = os.path.join(os.path.dirname(__file__), "..", "data", "companies.json")

log       = logging.getLogger("ci.competidores")
app       = func.FunctionApp()
apify     = ApifyClient()
yt        = YouTubeClient()
firecrawl = FirecrawlClient()


# ─────────────────────────────────────────────────────────────
# COMPANIES — carga y filtro por fuente
# ─────────────────────────────────────────────────────────────

def load_companies() -> list[dict]:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_activas(companies: list[dict], fuente: str) -> list[dict]:
    """
    Retorna las empresas que tienen la fuente configurada en companies.json.

    Lee el formato plano original:
      "instagram": "https://...",   → activa
      "instagram": null,            → inactiva, se salta
      "youtube_handle": "@handle",  → activa (campo especial para YouTube)
      "website": "https://...",     → activa para fuente "web"

    Cada elemento retornado incluye nombre, pais y url/handle de la fuente.
    """
    # Mapeo de nombre de fuente → campo en el JSON
    campo = {
        "youtube": "youtube_handle",
        "web":     "website",
    }.get(fuente, fuente)  # instagram, facebook, linkedin, tiktok, x, threads → mismo nombre

    result = []
    for c in companies:
        valor = c.get(campo)
        if not valor:  # null o campo inexistente → skip
            continue
        entry = {"nombre": c["nombre"], "pais": c.get("pais", "")}
        if fuente == "youtube":
            entry["handle"] = valor
        else:
            entry["url"] = valor
        result.append(entry)
    return result


# ─────────────────────────────────────────────────────────────
# ORQUESTADOR DELTA — reutilizable por todos los ingestores
# ─────────────────────────────────────────────────────────────

def _ingestar(
    fuente:     str,
    empresa:    str,
    items_raw:  list[dict],
    source_url: str = "",
    extra_meta: Optional[dict] = None,
) -> dict:
    """
    Núcleo de ingestión incremental reutilizable.

    Para cada empresa/fuente:
      1. Lee cursor delta actual
      2. Aplica filtro delta (hash + ids individuales)
      3. Guarda en Blob solo los items genuinamente nuevos
      4. Actualiza cursor
      5. Registra log de la ejecución

    Retorna dict resumen de la ejecución.
    """
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

    blob_paths  = []
    new_ids     = []
    errores     = 0

    for item in delta.items_new:
        item_id   = extract_item_id(item)
        blob_path = save_bronze(
            conn_str    = CONN_STR,
            container   = BRONZE_CONTAINER,
            fuente      = fuente,
            empresa     = empresa,
            item        = item,
            item_id     = item_id,
            variable_ic = "competidores",
            extra_meta  = extra_meta,
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
# INGESTORES POR FUENTE
# ─────────────────────────────────────────────────────────────

def ingest_instagram(companies: list[dict]) -> list[dict]:
    activas = get_activas(companies, "instagram")
    if not activas:
        return []

    urls      = [c["url"] for c in activas if c.get("url")]
    items_raw = apify.run_actor(ACTOR_INSTAGRAM, build_instagram_payload(urls))

    # Agrupar por username para delta individual por empresa
    por_cuenta: dict[str, list] = {}
    for item in items_raw:
        cuenta = item.get("ownerUsername") or item.get("username") or "desconocido"
        por_cuenta.setdefault(cuenta, []).append(item)

    resultados = []
    for cfg in activas:
        nombre   = cfg["nombre"]
        url      = cfg.get("url", "")
        username = url.rstrip("/").split("/")[-1] if url else None
        items_e  = por_cuenta.get(username, []) or items_raw
        resultados.append(_ingestar("instagram", nombre, items_e, source_url=url))
        time.sleep(0.1)

    return resultados


def ingest_facebook(companies: list[dict]) -> list[dict]:
    activas = get_activas(companies, "facebook")
    if not activas:
        return []

    urls      = [c["url"] for c in activas if c.get("url")]
    items_raw = apify.run_actor(ACTOR_FACEBOOK, build_facebook_payload(urls))

    por_pagina: dict[str, list] = {}
    for item in items_raw:
        pagina = item.get("pageName") or item.get("pageUsername") or "desconocido"
        por_pagina.setdefault(pagina, []).append(item)

    resultados = []
    for cfg in activas:
        nombre  = cfg["nombre"]
        slug    = cfg.get("url", "").rstrip("/").split("/")[-1]
        items_e = por_pagina.get(slug, []) or items_raw
        resultados.append(_ingestar("facebook", nombre, items_e, source_url=cfg.get("url", "")))
        time.sleep(0.1)

    return resultados


def ingest_tiktok(companies: list[dict]) -> list[dict]:
    activas   = get_activas(companies, "tiktok")
    usernames = []
    for c in activas:
        url = c.get("url", "")
        if url:
            u = url.rstrip("/").split("@")[-1]
            if u:
                usernames.append(u)
    if not usernames:
        return []

    items_raw = apify.run_actor(ACTOR_TIKTOK, build_tiktok_payload(usernames))

    por_author: dict[str, list] = {}
    for item in items_raw:
        meta   = item.get("authorMeta") or {}
        author = meta.get("uniqueId") or meta.get("name") or "desconocido"
        por_author.setdefault(author, []).append(item)

    resultados = []
    for cfg in activas:
        nombre   = cfg["nombre"]
        url      = cfg.get("url", "")
        username = url.rstrip("/").split("@")[-1] if url else None
        items_e  = por_author.get(username, []) or items_raw
        resultados.append(_ingestar("tiktok", nombre, items_e, source_url=url))
        time.sleep(0.1)

    return resultados


def ingest_linkedin(companies: list[dict]) -> list[dict]:
    activas = get_activas(companies, "linkedin")
    urls    = []
    for c in activas:
        li = c.get("url", "")
        if li and "/company/" in li:
            slug = li.split("/company/")[1].split("/")[0]
            urls.append(f"https://www.linkedin.com/company/{slug}/")
    if not urls:
        return []

    items_raw = apify.run_actor(ACTOR_LINKEDIN, build_linkedin_payload(urls))

    resultados = []
    for cfg in activas:
        nombre  = cfg["nombre"]
        li_url  = cfg.get("url", "")
        slug    = li_url.split("/company/")[1].split("/")[0] if "/company/" in li_url else ""
        items_e = [
            i for i in items_raw
            if slug and slug.lower() in (i.get("companyUrl") or "").lower()
        ] or items_raw
        resultados.append(_ingestar("linkedin", nombre, items_e, source_url=li_url))
        time.sleep(0.1)

    return resultados


def ingest_x(companies: list[dict]) -> list[dict]:
    """X: una llamada por empresa para delta preciso."""
    activas    = get_activas(companies, "x")
    resultados = []

    for cfg in activas:
        nombre = cfg["nombre"]
        url    = cfg.get("url", "")
        handle = url.rstrip("/").split("/")[-1] if url else None
        if not handle:
            continue

        items_raw = apify.run_actor(ACTOR_X, build_x_payload(handle))
        resultados.append(_ingestar("x", nombre, items_raw, source_url=url))
        apify.throttle()

    return resultados


def ingest_youtube(companies: list[dict]) -> list[dict]:
    activas    = get_activas(companies, "youtube")
    resultados = []

    for cfg in activas:
        nombre = cfg["nombre"]
        handle = cfg.get("handle") or cfg.get("url", "")
        if not handle:
            continue

        items_raw = yt.get_recent_videos(handle, max_results=5)
        resultados.append(_ingestar("youtube", nombre, items_raw, source_url=handle))
        apify.throttle()

    return resultados


def ingest_web(companies: list[dict]) -> list[dict]:
    """
    Web scraping semanal usando Firecrawl.
    Solo guarda Markdown crudo en Bronze — sin IA.
    """
    activas    = get_activas(companies, "web")
    resultados = []

    for cfg in activas:
        nombre  = cfg["nombre"]
        website = cfg.get("url", "")
        if not website:
            continue

        urls      = build_company_urls(website)
        items_raw = firecrawl.scrape_many(urls)

        resultados.append(_ingestar(
            fuente     = "web",
            empresa    = nombre,
            items_raw  = items_raw,
            source_url = website,
        ))
        firecrawl.throttle()

    return resultados


def ingest_noticias_competidores(companies: list[dict]) -> list[dict]:
    """
    Noticias y comunicados de prensa de cada competidor.

    Scrape de la sección de noticias/prensa de la web corporativa.
    Detecta lanzamientos, alianzas, premios y movimientos estratégicos
    antes de que lleguen a medios generales.

    Frecuencia: semanal.
    """
    activas    = get_activas(companies, "web")
    resultados = []

    paths_noticias = [
        "/noticias", "/news", "/sala-de-prensa", "/press",
        "/blog", "/insights", "/comunicados", "/novedades",
    ]

    for cfg in activas:
        nombre  = cfg["nombre"]
        website = cfg.get("url", "")
        if not website:
            continue

        urls = [website.rstrip("/") + path for path in paths_noticias]
        items_raw = firecrawl.scrape_many(urls)

        resultados.append(_ingestar(
            fuente     = "noticias_competidor",
            empresa    = nombre,
            items_raw  = items_raw,
            source_url = website,
        ))
        firecrawl.throttle()

    return resultados


def ingest_github_competidores(companies: list[dict]) -> list[dict]:
    """
    Repositorios GitHub de los competidores.

    Detecta qué tecnologías están desarrollando internamente,
    proyectos open source y nivel de actividad técnica.

    Frecuencia: semanal.
    """
    from shared.github_client import GitHubClient
    gh         = GitHubClient()
    resultados = []

    for company in companies:
        nombre = company["nombre"]
        # Buscar repos por nombre de empresa
        nombre_corto = nombre.split()[0].lower()
        if nombre_corto in {"productive", "pricewaterhousecoopers", "tata"}:
            nombre_corto = nombre.split()[1].lower() if len(nombre.split()) > 1 else nombre_corto

        items_raw = gh.get_trending_repos(
            topic    = nombre_corto,
            dias     = 90,
            limite   = 5,
            min_stars= 1,
        )
        # Solo guardar si encontró repos reales de la empresa
        items_filtrados = [
            i for i in items_raw
            if nombre_corto in (i.get("full_name") or "").lower()
            or nombre_corto in (i.get("owner", {}).get("login") or "").lower()
        ]
        if items_filtrados:
            resultados.append(_ingestar(
                fuente     = "github_competidor",
                empresa    = nombre,
                items_raw  = items_filtrados,
                source_url = f"https://github.com/{nombre_corto}",
            ))
        gh.throttle()

    return resultados


# ─────────────────────────────────────────────────────────────
# MAPA DE FUENTES — para ejecución manual y timers
# ─────────────────────────────────────────────────────────────
FUENTES = {
    "instagram":             ingest_instagram,
    "facebook":              ingest_facebook,
    "tiktok":                ingest_tiktok,
    "linkedin":              ingest_linkedin,
    "x":                     ingest_x,
    "youtube":               ingest_youtube,
    "web":                   ingest_web,
    "noticias_competidores": ingest_noticias_competidores,
    "github_competidores":   ingest_github_competidores,
}


def _ensure_tables() -> None:
    try:
        init_delta_table(CONN_STR)
    except Exception as ex:
        log.warning("Tablas de control: %s", ex)


# ─────────────────────────────────────────────────────────────
# TRIGGERS — Timer (escalonados, Colombia UTC-5)
# ─────────────────────────────────────────────────────────────

@app.timer_trigger(schedule="0 0 11 * * *", arg_name="timer", run_on_startup=False)
def timer_instagram(timer: func.TimerRequest) -> None:
    """Instagram — 6:00am Colombia (11:00 UTC)."""
    _ensure_tables()
    log.info("Bronze Instagram: %s", ingest_instagram(load_companies()))


@app.timer_trigger(schedule="0 10 11 * * *", arg_name="timer", run_on_startup=False)
def timer_facebook(timer: func.TimerRequest) -> None:
    """Facebook — 6:10am Colombia."""
    _ensure_tables()
    log.info("Bronze Facebook: %s", ingest_facebook(load_companies()))


@app.timer_trigger(schedule="0 20 11 * * *", arg_name="timer", run_on_startup=False)
def timer_tiktok(timer: func.TimerRequest) -> None:
    """TikTok — 6:20am Colombia."""
    _ensure_tables()
    log.info("Bronze TikTok: %s", ingest_tiktok(load_companies()))


@app.timer_trigger(schedule="0 30 11 * * *", arg_name="timer", run_on_startup=False)
def timer_linkedin(timer: func.TimerRequest) -> None:
    """LinkedIn — 6:30am Colombia."""
    _ensure_tables()
    log.info("Bronze LinkedIn: %s", ingest_linkedin(load_companies()))


@app.timer_trigger(schedule="0 40 11 * * *", arg_name="timer", run_on_startup=False)
def timer_x(timer: func.TimerRequest) -> None:
    """X/Twitter — 6:40am Colombia."""
    _ensure_tables()
    log.info("Bronze X: %s", ingest_x(load_companies()))


@app.timer_trigger(schedule="0 50 11 * * *", arg_name="timer", run_on_startup=False)
def timer_youtube(timer: func.TimerRequest) -> None:
    """YouTube — 6:50am Colombia."""
    _ensure_tables()
    log.info("Bronze YouTube: %s", ingest_youtube(load_companies()))


@app.timer_trigger(schedule="0 0 7 * * 0", arg_name="timer", run_on_startup=False)
def timer_web(timer: func.TimerRequest) -> None:
    """Web scraping — domingos 2:00am Colombia (7:00 UTC)."""
    _ensure_tables()
    log.info("Bronze Web: %s", ingest_web(load_companies()))


@app.timer_trigger(schedule="0 30 7 * * 0", arg_name="timer", run_on_startup=False)
def timer_noticias_competidores(timer: func.TimerRequest) -> None:
    """Noticias/prensa de competidores — domingos 2:30am Colombia (7:30 UTC)."""
    _ensure_tables()
    log.info("Bronze Noticias: %s", ingest_noticias_competidores(load_companies()))


@app.timer_trigger(schedule="0 0 8 * * 0", arg_name="timer", run_on_startup=False)
def timer_github_competidores(timer: func.TimerRequest) -> None:
    """GitHub de competidores — domingos 3:00am Colombia (8:00 UTC)."""
    _ensure_tables()
    log.info("Bronze GitHub: %s", ingest_github_competidores(load_companies()))


# ─────────────────────────────────────────────────────────────
# HTTP TRIGGERS — ejecución manual y observabilidad
# ─────────────────────────────────────────────────────────────

@app.route(route="competidores/ejecutar", methods=["GET", "POST"])
def ejecutar(req: func.HttpRequest) -> func.HttpResponse:
    """
    Ejecuta una fuente manualmente.
    GET /api/competidores/ejecutar?fuente=instagram
    GET /api/competidores/ejecutar?fuente=todas
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

    companies  = load_companies()
    resultados = []
    targets    = FUENTES.items() if fuente == "todas" else [(fuente, FUENTES[fuente])]

    for nombre_f, fn in targets:
        log.info("Manual: ejecutando %s", nombre_f)
        res = fn(companies)
        resultados.extend(res if isinstance(res, list) else [res])
        apify.throttle()

    return func.HttpResponse(
        json.dumps({"resultados": resultados}, ensure_ascii=False, default=str),
        status_code=200, mimetype="application/json",
    )


@app.route(route="competidores/status", methods=["GET"])
def status(req: func.HttpRequest) -> func.HttpResponse:
    """
    Observabilidad del dominio competidores.

    GET /api/competidores/status              → cursores delta de todas las fuentes/empresas
    GET /api/competidores/status?fuente=x     → últimas ejecuciones de esa fuente
    GET /api/competidores/status?modo=blobs   → blobs recientes en Blob Storage
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


@app.route(route="competidores/reset_cursor", methods=["POST"])
def reset(req: func.HttpRequest) -> func.HttpResponse:
    """
    Resetea el cursor delta de una empresa/fuente.
    Fuerza re-ingestión completa en la próxima ejecución.
    POST /api/competidores/reset_cursor?fuente=instagram&empresa=Accenture
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
