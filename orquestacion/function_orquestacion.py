"""
function_orquestacion.py
========================
Orquestador del pipeline completo dentro de Azure:

    BRONZE -> SILVER -> GEMINI -> EVENTOS -> GOLD -> TABLERO

QUÉ HACE: ejecuta todas las capas en cadena, un PASO a la vez. Cada paso es
un mensaje en la cola 'pipeline-pasos': la función toma el mensaje, ejecuta
ese paso y deja en la cola el mensaje del paso siguiente. Así ningún paso
choca con el límite de tiempo de Azure y la corrida avanza sola hasta el final.

CONFIGURACIÓN (Function App -> Configuración -> Variables de entorno).
Se cambia sin redesplegar; la siguiente corrida ya toma los valores nuevos:

  PIPELINE_HORA                  Hora de Colombia a la que corre, de 0 a 23.
                                 2 = 2:00 a.m., 14 = 2:00 p.m.        (defecto: 2)
  PIPELINE_CADA_DIAS             Cada cuántos días corre. 1 = todos los días,
                                 7 = una vez por semana, 0 = PAUSADO.  (defecto: 1)
  PIPELINE_FIRECRAWL_CADA_DIAS   Cada cuántos días se incluyen las páginas web
                                 (Firecrawl, 1.000 páginas gratis al mes).
                                 0 = nunca.                            (defecto: 14)
  PIPELINE_INCLUIR_APIFY         true / false. Redes sociales y LinkedIn
                                 (CUESTAN crédito de Apify).           (defecto: false)

EJECUCIÓN MANUAL:  GET /api/pipeline/ejecutar   (respeta la misma configuración)
  Parámetros opcionales, solo para ESA corrida:
    apify=1 / 0       fuerza incluir o no Apify
    firecrawl=1 / 0   fuerza incluir o no Firecrawl
    fases=a,b         solo esas fases: bronze, silver, gemini, eventos, gold, tablero
    forzar=1          arranca aunque haya otra corrida en curso
CONSULTA:  GET /api/pipeline/estado  -> avance, último resultado y configuración actual.

REGLA DE FALLOS: si un paso falla, se registra y la corrida SIGUE. Una fuente
caída no debe dejar el tablero sin actualizar. El estado final dice
"completada" o "completada con errores".

DÓNDE QUEDA EL REGISTRO (cuenta de datos, stinteligencia):
  pipelineEstado  -> estado de la corrida actual/última y fechas de la última
                     corrida automática y de la última con Firecrawl
  pipelineLog     -> una fila por paso ejecutado (PartitionKey = id de corrida)
"""

from __future__ import annotations

import os
import sys
import json
import time
import logging
import importlib
import subprocess
from datetime import datetime, timezone, timedelta

import azure.functions as func

bp = func.Blueprint()
log = logging.getLogger("orquestador")

COLA = "pipeline-pasos"
CONN_COLA = "AzureWebJobsStorage"          # la cola vive en la cuenta interna del runtime
CONN_DATOS = "AZURE_STORAGE_CONNECTION_STRING"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TAM_TANDA_SILVER = 200
TIMEOUT_SCRIPT = 100 * 60                   # 100 min por script (Gemini es el más largo)
HORAS_CORRIDA_COLGADA = 12                  # pasado esto, una corrida "en_curso" se da por muerta
UTC_COLOMBIA = -5                           # Colombia no tiene horario de verano

# ─────────────────────────────────────────────────────────────
# DEFINICIÓN DEL PIPELINE (mismo orden que el orquestador local)
# ─────────────────────────────────────────────────────────────

BRONZE_GRATIS = [
    ("clientes", "clientes_conocidos"), ("clientes", "secop_licitaciones"),
    ("competidores", "secop_competidores"), ("competidores", "github_competidores"),
    ("competidores", "noticias_competidores"), ("competidores", "youtube"),
    ("mercado", "noticias_economicas"),
    ("tendencias", "github_trending"), ("tendencias", "rss_fabricantes"),
    ("tendencias", "rss_tech_general"), ("tendencias", "rss_colombia"),
    ("proveedores", "rss_proveedores"), ("proveedores", "rss_ecosistema"),
    ("proveedores", "startups_tech_colombia"),
    ("regulatorio", "noticias_regulatorio"),
    ("competidores", "web"),
    ("mercado", "dane_estadisticas"), ("mercado", "reportes_economicos"),
    ("mercado", "ontic_barreras_adopcion"),
    ("proveedores", "web_proveedores_core"), ("proveedores", "licenciamiento"),
    ("proveedores", "noticias_proveedores"),
    ("capacidades", "web_tak"), ("capacidades", "certificaciones_fabricantes"),
    ("tendencias", "reportes_sectoriales"), ("tendencias", "documentacion_tecnica"),
    ("tendencias", "eventos_tech"), ("tendencias", "patentes_tech"),
    ("regulatorio", "mintic"), ("regulatorio", "normativa_tic"),
    ("regulatorio", "sic_datos"), ("regulatorio", "colombia_compra"),
    ("regulatorio", "financiamiento_tech"), ("regulatorio", "inversion_tech"),
    ("clientes-competencia", "clientes_competencia"),
]

BRONZE_APIFY = [
    ("competidores", "instagram"), ("competidores", "facebook"),
    ("competidores", "tiktok"), ("competidores", "linkedin"), ("competidores", "x"),
    ("clientes", "linkedin_jobs"), ("capacidades", "demanda_perfiles_ti"),
    ("mercado", "redes_sociales_mercado"), ("proveedores", "redes_sociales_proveedores"),
    ("tendencias", "redes_sociales_tech"),
]

# Fuentes que usan Firecrawl (páginas web). Solo corren cuando toca según
# PIPELINE_FIRECRAWL_CADA_DIAS. Las demás fuentes de Bronze son gratuitas.
# (clientes_conocidos y rss_fabricantes usan unas pocas páginas de respaldo,
#  pero también traen SECOP y noticias, así que corren todos los días.)
FUENTES_FIRECRAWL = {
    ("competidores", "web"),
    ("mercado", "dane_estadisticas"), ("mercado", "reportes_economicos"),
    ("mercado", "ontic_barreras_adopcion"),
    ("proveedores", "web_proveedores_core"), ("proveedores", "licenciamiento"),
    ("proveedores", "noticias_proveedores"),
    ("capacidades", "web_tak"), ("capacidades", "certificaciones_fabricantes"),
    ("tendencias", "reportes_sectoriales"), ("tendencias", "documentacion_tecnica"),
    ("tendencias", "eventos_tech"), ("tendencias", "patentes_tech"),
    ("regulatorio", "mintic"), ("regulatorio", "normativa_tic"),
    ("regulatorio", "sic_datos"), ("regulatorio", "colombia_compra"),
    ("regulatorio", "financiamiento_tech"), ("regulatorio", "inversion_tech"),
}

CONTENEDORES_BRONZE = [
    "bronze-competidores", "bronze-clientes", "bronze-clientes-competencia",
    "bronze-mercado", "bronze-proveedores", "bronze-regulatorio",
    "bronze-tendencias", "bronze-capacidades",
]

# Scripts: (fase, nombre visible, carpeta, script, argumentos)
SCRIPTS = [
    ("gemini",  "Clasificación tema/sentimiento (Gemini)", ".", "clasificar_silver_gemini.py", ["--limite", "450"]),
    ("eventos", "Clasificación de eventos (Groq)", "ingestion_silver/clasificacion_eventos", "clasificar_eventos.py", ["--todos"]),
    ("gold",    "Gold analítico (indicadores y veredictos)", "gold", "calcular_gold.py", []),
    ("tablero", "goldContratos",              ".", "generar_contratos.py",        ["--escribir"]),
    ("tablero", "goldLicitaciones",           ".", "generar_licitaciones.py",     ["--escribir"]),
    ("tablero", "goldMercadoNoticias",        ".", "generar_mercado_noticias.py", ["--escribir"]),
    ("tablero", "goldCifrasMercado",          ".", "extraer_cifras_mercado.py",   ["--escribir"]),
    ("tablero", "goldRadarTecnologias",       ".", "generar_radar_tecnologias.py", ["--escribir"]),
    ("tablero", "goldTecnologias",            ".", "generar_tecnologias.py",      ["--escribir"]),
    ("tablero", "goldComparativoServicios",   ".", "comparativo_servicios.py",    ["--escribir"]),
    ("tablero", "goldRecomendacionContratos", ".", "recomendacion_contratos.py",  []),
    ("tablero", "goldRecomendacionMercado",   ".", "recomendacion_mercado.py",    []),
    ("tablero", "goldVentas",                 ".", "generar_ventas.py",           ["--escribir"]),
    ("tablero", "goldAlertasCentro",          ".", "generar_alertas.py",          ["--escribir"]),
]

FASES = ["bronze", "silver", "gemini", "eventos", "gold", "tablero"]


# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN (variables de entorno, editables en el portal)
# ─────────────────────────────────────────────────────────────

def _entero(nombre: str, defecto: int, minimo: int, maximo: int) -> int:
    try:
        v = int(str(os.environ.get(nombre, defecto)).split(":")[0].strip())
    except ValueError:
        v = defecto
    return max(minimo, min(maximo, v))


def leer_config() -> dict:
    """QUÉ HACE: lee los 4 valores que configura el cliente (con valores seguros por defecto)."""
    return {
        "hora": _entero("PIPELINE_HORA", 2, 0, 23),
        "cada_dias": _entero("PIPELINE_CADA_DIAS", 1, 0, 365),
        "firecrawl_cada_dias": _entero("PIPELINE_FIRECRAWL_CADA_DIAS", 14, 0, 365),
        "apify": os.environ.get("PIPELINE_INCLUIR_APIFY", "false").strip().lower() in ("true", "1", "si", "sí"),
    }


def hoy_colombia() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=UTC_COLOMBIA)


def _dias_desde(fecha_txt: str | None) -> int | None:
    if not fecha_txt:
        return None
    try:
        return (hoy_colombia().date() - datetime.strptime(fecha_txt, "%Y-%m-%d").date()).days
    except ValueError:
        return None


def toca_firecrawl(est: dict | None, cfg: dict) -> bool:
    """QUÉ HACE: decide si en esta corrida se incluyen las fuentes de Firecrawl."""
    if cfg["firecrawl_cada_dias"] == 0:
        return False
    dias = _dias_desde((est or {}).get("ultima_firecrawl"))
    return dias is None or dias >= cfg["firecrawl_cada_dias"]


def armar_pasos(apify: bool, firecrawl: bool, fases: list[str]) -> list[dict]:
    """QUÉ HACE: arma la lista ordenada de pasos de una corrida."""
    pasos = []
    if "bronze" in fases:
        for dom, fuente in BRONZE_GRATIS + (BRONZE_APIFY if apify else []):
            if (dom, fuente) in FUENTES_FIRECRAWL and not firecrawl:
                continue
            pasos.append({"tipo": "bronze", "nombre": f"Bronze {dom}/{fuente}", "dom": dom, "fuente": fuente})
    if "silver" in fases:
        pasos.append({"tipo": "silver", "nombre": "Silver (8 contenedores)"})
    for fase, nombre, carpeta, script, args in SCRIPTS:
        if fase in fases:
            pasos.append({"tipo": "script", "nombre": nombre, "carpeta": carpeta, "script": script, "args": args})
    return pasos


# ─────────────────────────────────────────────────────────────
# REGISTRO (tablas en stinteligencia)
# ─────────────────────────────────────────────────────────────

def _tabla(nombre: str):
    from azure.data.tables import TableServiceClient
    ts = TableServiceClient.from_connection_string(os.environ[CONN_DATOS])
    return ts.create_table_if_not_exists(nombre)


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def leer_estado() -> dict | None:
    try:
        e = _tabla("pipelineEstado").get_entity("pipeline", "actual")
        return dict(e)
    except Exception:
        return None


def guardar_estado(**campos) -> None:
    from azure.data.tables import UpdateMode
    _tabla("pipelineEstado").upsert_entity(
        {"PartitionKey": "pipeline", "RowKey": "actual", **campos}, mode=UpdateMode.MERGE)


def registrar_paso(corrida: str, i: int, nombre: str, ok: bool, detalle: str, segundos: float) -> None:
    try:
        _tabla("pipelineLog").upsert_entity({
            "PartitionKey": corrida, "RowKey": f"{i:06d}",
            "paso": nombre, "ok": ok, "segundos": round(segundos),
            "detalle": (detalle or "")[-30000:],        # límite de tamaño de Table Storage
            "fecha": _ahora(),
        })
    except Exception as ex:
        log.warning("No se pudo registrar el paso %s: %s", nombre, ex)


# ─────────────────────────────────────────────────────────────
# EJECUCIÓN DE CADA TIPO DE PASO
# ─────────────────────────────────────────────────────────────

def correr_bronze(dom: str, fuente: str) -> tuple[bool, str]:
    """QUÉ HACE: llama la función de la fuente directamente (la misma que usa /<dominio>/ejecutar)."""
    modulo = dom.replace("-", "_")
    mod = importlib.import_module(f"ingestion_{modulo}.function_{modulo}")
    if hasattr(mod, "_ensure_tables"):
        mod._ensure_tables()
    fn = mod.FUENTES[fuente]
    res = fn(mod.load_companies()) if dom == "competidores" else fn()
    return True, json.dumps(res, ensure_ascii=False, default=str)[:3000]


def correr_silver_tanda(c_idx: int, desde: int) -> tuple[bool, str, int | None, int]:
    """
    QUÉ HACE: procesa UNA tanda de un contenedor.
    Devuelve (ok, detalle, siguiente_contenedor, siguiente_desde); siguiente_contenedor
    es None cuando ya no quedan contenedores.
    """
    from ingestion_silver.function_silver import procesar_tanda
    cont = CONTENEDORES_BRONZE[c_idx]
    r = procesar_tanda(cont, desde=desde, hasta=desde + TAM_TANDA_SILVER)
    ok = "error" not in r and r.get("errores", 0) == 0
    detalle = json.dumps({k: v for k, v in r.items() if k != "detalle_errores"}, ensure_ascii=False)
    total = r.get("total_en_contenedor", 0)
    if "error" in r or desde + TAM_TANDA_SILVER >= total:
        sig_c = c_idx + 1
        return ok, detalle, (sig_c if sig_c < len(CONTENEDORES_BRONZE) else None), 0
    return ok, detalle, c_idx, desde + TAM_TANDA_SILVER


def correr_script(carpeta: str, script: str, args: list[str]) -> tuple[bool, str]:
    """
    QUÉ HACE: ejecuta un script del proyecto como subproceso, igual que en la terminal.
    PYTHONPATH lleva las librerías instaladas de la Function para que el script las encuentre.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    env["PYTHONIOENCODING"] = "utf-8"
    cwd = os.path.join(RAIZ, carpeta)
    r = subprocess.run([sys.executable, script] + list(args), cwd=cwd, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=TIMEOUT_SCRIPT)
    salida = (r.stdout or "") + ("\n[stderr]\n" + r.stderr if r.stderr else "")
    return r.returncode == 0, salida


# ─────────────────────────────────────────────────────────────
# ARRANQUE DE UNA CORRIDA
# ─────────────────────────────────────────────────────────────

def iniciar_corrida(apify: bool, firecrawl: bool, fases: list[str], forzar: bool,
                    origen: str) -> tuple[dict, str | None]:
    """QUÉ HACE: crea la corrida y devuelve (respuesta, mensaje_primer_paso)."""
    est = leer_estado()
    if est and est.get("estado") == "en_curso" and not forzar:
        inicio = datetime.strptime(est.get("inicio", "2000-01-01 00:00:00 UTC"), "%Y-%m-%d %H:%M:%S UTC")
        horas = (datetime.now(timezone.utc).replace(tzinfo=None) - inicio).total_seconds() / 3600
        if horas < HORAS_CORRIDA_COLGADA:
            return ({"error": "Ya hay una corrida en curso. Consulta /api/pipeline/estado "
                              "o usa forzar=1.", "corrida": est.get("corrida")}, None)

    pasos = armar_pasos(apify, firecrawl, fases)
    if not pasos:
        return ({"error": f"Ninguna fase válida. Opciones: {FASES}"}, None)

    corrida = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    extra = {}
    if firecrawl and "bronze" in fases:
        extra["ultima_firecrawl"] = hoy_colombia().strftime("%Y-%m-%d")
    guardar_estado(corrida=corrida, estado="en_curso", origen=origen, inicio=_ahora(), fin="",
                   fases=",".join(fases), apify=apify, firecrawl=firecrawl,
                   total_pasos=len(pasos), paso_actual=0, nombre_paso=pasos[0]["nombre"],
                   pasos_con_error=0, errores="", **extra)
    msg = json.dumps({"corrida": corrida, "i": 0, "apify": apify, "firecrawl": firecrawl, "fases": fases})
    return ({"corrida": corrida, "estado": "en_curso", "total_pasos": len(pasos), "fases": fases,
             "incluye_apify": apify, "incluye_firecrawl": firecrawl,
             "consultar": "/api/pipeline/estado"}, msg)


def _fases_param(texto: str | None) -> list[str]:
    if not texto:
        return list(FASES)
    pedidas = [f.strip().lower() for f in texto.split(",")]
    return [f for f in FASES if f in pedidas]


# ─────────────────────────────────────────────────────────────
# TRIGGERS
# ─────────────────────────────────────────────────────────────

@bp.route(route="pipeline/ejecutar", methods=["GET", "POST"])
@bp.queue_output(arg_name="siguiente", queue_name=COLA, connection=CONN_COLA)
def pipeline_ejecutar(req: func.HttpRequest, siguiente: func.Out[str]) -> func.HttpResponse:
    """Arranca una corrida manual y responde de inmediato (la corrida sigue sola por la cola)."""
    cfg = leer_config()
    p_apify, p_fc = req.params.get("apify"), req.params.get("firecrawl")
    resp, msg = iniciar_corrida(
        apify=(p_apify == "1") if p_apify in ("0", "1") else cfg["apify"],
        firecrawl=(p_fc == "1") if p_fc in ("0", "1") else toca_firecrawl(leer_estado(), cfg),
        fases=_fases_param(req.params.get("fases")),
        forzar=req.params.get("forzar", "0") == "1",
        origen="manual",
    )
    if msg:
        siguiente.set(msg)
    return func.HttpResponse(json.dumps(resp, ensure_ascii=False, indent=2),
                             status_code=202 if msg else 409, mimetype="application/json")


@bp.timer_trigger(schedule="0 0 * * * *", arg_name="timer", run_on_startup=False)
@bp.queue_output(arg_name="siguiente", queue_name=COLA, connection=CONN_COLA)
def pipeline_programado(timer: func.TimerRequest, siguiente: func.Out[str]) -> None:
    """
    Revisa cada hora en punto si toca correr, según PIPELINE_HORA y PIPELINE_CADA_DIAS.
    (Revisar no cuesta nada; solo arranca la corrida a la hora configurada.)
    """
    cfg = leer_config()
    if cfg["cada_dias"] == 0:
        return                                          # pausado por configuración
    ahora = hoy_colombia()
    if ahora.hour != cfg["hora"]:
        return
    est = leer_estado() or {}
    dias = _dias_desde(est.get("ultima_auto"))
    if dias is not None and dias < cfg["cada_dias"]:
        return                                          # ya corrió dentro del intervalo
    resp, msg = iniciar_corrida(apify=cfg["apify"], firecrawl=toca_firecrawl(est, cfg),
                                fases=list(FASES), forzar=False, origen="programada")
    log.info("Pipeline programado: %s", resp)
    if msg:
        guardar_estado(ultima_auto=ahora.strftime("%Y-%m-%d"))
        siguiente.set(msg)


@bp.queue_trigger(arg_name="mensaje", queue_name=COLA, connection=CONN_COLA)
@bp.queue_output(arg_name="siguiente", queue_name=COLA, connection=CONN_COLA)
def pipeline_paso(mensaje: func.QueueMessage, siguiente: func.Out[str]) -> None:
    """Ejecuta UN paso y encola el siguiente. Nunca lanza excepción: un fallo no se reintenta."""
    m = json.loads(mensaje.get_body().decode("utf-8"))
    corrida, i = m["corrida"], m["i"]

    est = leer_estado() or {}
    if est.get("corrida") != corrida or est.get("estado") != "en_curso":
        log.info("Mensaje de una corrida que ya no está activa (%s); se descarta.", corrida)
        return

    pasos = armar_pasos(m.get("apify", False), m.get("firecrawl", True), m.get("fases", FASES))
    if i >= len(pasos):
        return
    paso = pasos[i]
    nombre = paso["nombre"]
    sig = None                              # mensaje del paso siguiente
    t0 = time.time()

    try:
        if paso["tipo"] == "bronze":
            ok, detalle = correr_bronze(paso["dom"], paso["fuente"])
        elif paso["tipo"] == "silver":
            c_idx, desde = m.get("c", 0), m.get("desde", 0)
            nombre = f"Silver {CONTENEDORES_BRONZE[c_idx]} {desde}-{desde + TAM_TANDA_SILVER}"
            ok, detalle, sig_c, sig_desde = correr_silver_tanda(c_idx, desde)
            if sig_c is not None:           # quedan tandas: el siguiente mensaje sigue en Silver
                sig = {**m, "c": sig_c, "desde": sig_desde}
        else:
            ok, detalle = correr_script(paso["carpeta"], paso["script"], paso["args"])
    except Exception as ex:                 # noqa: BLE001
        ok, detalle = False, f"{type(ex).__name__}: {ex}"

    dur = time.time() - t0
    rk = i * 1000 + (m.get("c", 0) * 100 + m.get("desde", 0) // TAM_TANDA_SILVER if paso["tipo"] == "silver" else 0)
    registrar_paso(corrida, rk, nombre, ok, detalle, dur)
    log.info("Paso %s/%s %s -> %s (%.0fs)", i + 1, len(pasos), nombre, "OK" if ok else "FALLO", dur)

    errores = est.get("errores", "") or ""
    con_error = int(est.get("pasos_con_error", 0) or 0)
    if not ok:
        con_error += 1
        errores = (errores + f"{nombre}; ")[-2000:]

    if sig is None:
        sig = {k: v for k, v in m.items() if k not in ("c", "desde")}
        sig["i"] = i + 1

    if sig["i"] < len(pasos):
        guardar_estado(paso_actual=sig["i"], nombre_paso=pasos[sig["i"]]["nombre"],
                       pasos_con_error=con_error, errores=errores)
        siguiente.set(json.dumps(sig))
    else:
        guardar_estado(paso_actual=len(pasos), nombre_paso="", pasos_con_error=con_error,
                       errores=errores, fin=_ahora(),
                       estado="completada" if con_error == 0 else "completada con errores")
        log.info("Corrida %s terminada (%s pasos con error).", corrida, con_error)


@bp.route(route="pipeline/estado", methods=["GET"])
def pipeline_estado(req: func.HttpRequest) -> func.HttpResponse:
    """Estado de la corrida actual o la última, con los últimos pasos ejecutados."""
    cfg = leer_config()
    est = leer_estado() or {}
    dias_fc = _dias_desde(est.get("ultima_firecrawl"))
    config_txt = {
        "hora_colombia": f"{cfg['hora']:02d}:00",
        "frecuencia": ("PAUSADO" if cfg["cada_dias"] == 0 else
                       "todos los días" if cfg["cada_dias"] == 1 else f"cada {cfg['cada_dias']} días"),
        "firecrawl": ("nunca" if cfg["firecrawl_cada_dias"] == 0 else
                      f"cada {cfg['firecrawl_cada_dias']} días"),
        "firecrawl_en_proxima_corrida": toca_firecrawl(est, cfg),
        "dias_desde_ultima_firecrawl": dias_fc,
        "apify": "incluido" if cfg["apify"] else "apagado",
    }
    if not est:
        return func.HttpResponse(json.dumps({"estado": "sin corridas", "configuracion": config_txt},
                                            ensure_ascii=False, indent=2), mimetype="application/json")
    est = {k: v for k, v in est.items() if k not in ("PartitionKey", "RowKey")}
    est["configuracion"] = config_txt
    try:
        filas = list(_tabla("pipelineLog").query_entities(f"PartitionKey eq '{est['corrida']}'"))
        filas.sort(key=lambda e: e["RowKey"])
        est["ultimos_pasos"] = [
            {"paso": e["paso"], "ok": e["ok"], "segundos": e["segundos"], "fecha": e["fecha"]}
            for e in filas[-10:]
        ]
    except Exception:
        est["ultimos_pasos"] = []
    return func.HttpResponse(json.dumps(est, ensure_ascii=False, indent=2, default=str),
                             mimetype="application/json")