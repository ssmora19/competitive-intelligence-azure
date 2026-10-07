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
  PIPELINE_DIAS_SEMANA           Días de la semana en que SÍ corre, separados por coma:
                                 lun,mar,mie,jue,vie,sab,dom. Vacío = todos.
                                 Ej.: lun,mar,mie,jue = solo de lunes a jueves.
  PIPELINE_DESDE / PIPELINE_HASTA  Fechas (AAAA-MM-DD) entre las que corre. Vacías =
                                 sin límite. Ej.: DESDE=2026-11-02 y HASTA=2026-11-05
                                 = corre solo esos 4 días y después se detiene sola.

EJECUCIÓN MANUAL:  GET /api/pipeline/ejecutar   (respeta la misma configuración)
  Parámetros opcionales, solo para ESA corrida:
    apify=1 / 0       fuerza incluir o no Apify
    firecrawl=1 / 0   fuerza incluir o no Firecrawl
    fases=a,b         solo esas fases: bronze, silver, gemini, eventos, gold, tablero
    silver_completo=1 Silver reprocesa TODA la historia (por defecto solo lo nuevo
                      desde la última corrida de Silver sin errores)
    forzar=1          arranca aunque haya otra corrida en curso
CONSULTA:  GET /api/pipeline/estado  -> avance, último resultado y configuración actual.

PASOS CORTOS: Gemini va en 6 tramos de 75 noticias y la clasificación de eventos
en 3 tramos de 50. Si Azure recicla el servidor a mitad de un paso, se pierde a lo
sumo un tramo corto (unos minutos), no una hora de trabajo. Si un tramo informa
que no queda nada pendiente o que se agotó la cuota del día, los tramos restantes
de esa fase se saltan (no tiene sentido llamar a la IA otra vez).

VIGILANTE AUTOMÁTICO: el reloj que revisa cada 15 minutos también comprueba que la
corrida en curso siga avanzando. Si lleva más de MINUTOS_SIN_AVANCE sin avanzar
(el servidor se reinició y el paso se perdió), la REANUDA desde el paso donde quedó.
Si el mismo paso se pierde MAX_REANUDAR_PASO veces, lo marca como fallido y sigue
con el siguiente, para que el tablero se actualice igual. Tras
MAX_REANUDACIONES_CORRIDA reanudaciones en una misma corrida la da por
"interrumpida" (algo más grave está pasando y hay que revisarlo).

REGLA DE FALLOS: si un paso falla, se registra y la corrida SIGUE. Un script que
termina pero choca con el límite diario de Groq (error 429) también cuenta como
fallo, porque deja datos de relleno. Una fuente
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
MARGEN_SILVER_DIAS = 1                      # Silver incremental: relee 1 día extra por seguridad
TIMEOUT_SCRIPT = 100 * 60                   # 100 min por script (Gemini es el más largo)
HORAS_CORRIDA_COLGADA = 12                  # pasado esto, una corrida "en_curso" se da por muerta
MINUTOS_SIN_AVANCE = 130                    # > functionTimeout (120 min): ningún paso vivo dura tanto
MAX_REANUDAR_PASO = 3                       # veces que se reintenta un mismo paso perdido
MAX_REANUDACIONES_CORRIDA = 8               # reanudaciones totales antes de "interrumpida"
TRAMOS_GEMINI, TAM_TRAMO_GEMINI = 6, 75     # 6 x 75 = 450 noticias/día (cuota gratis de Gemini)
TRAMOS_EVENTOS, TAM_TRAMO_EVENTOS = 3, 50   # 3 x 50 = 150 eventos/día (cupo de Groq)
# Textos que, si aparecen en la salida de un tramo, hacen saltar los tramos restantes
# de esa fase: no queda nada por hacer o la IA ya no recibe más llamadas hoy.
FIN_DE_GRUPO = ("Nada pendiente", "CUOTA DIARIA AGOTADA", "límite alcanzado",
                "LÍMITE DE GROQ ALCANZADO", "TOTAL procesados=0 ")
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
    *[("gemini", f"Clasificación de tema (Gemini) tramo {k}/{TRAMOS_GEMINI}", ".",
       "clasificar_silver_gemini.py", ["--limite", str(TAM_TRAMO_GEMINI)])
      for k in range(1, TRAMOS_GEMINI + 1)],
    *[("eventos", f"Clasificación de eventos (Groq) tramo {k}/{TRAMOS_EVENTOS}",
       "ingestion_silver/clasificacion_eventos", "clasificar_eventos.py",
       ["--todos", "--limite", str(TAM_TRAMO_EVENTOS)])
      for k in range(1, TRAMOS_EVENTOS + 1)],
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
    # Retención: borra bitácoras con más de 180 días (los archivos viejos de Bronze y
    # Silver los borra la política de ciclo de vida de la cuenta de almacenamiento).
    ("tablero", "Depuración de registros antiguos", ".", "depurar_registros.py", ["--escribir"]),
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


DIAS_SEMANA = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]   # lunes = 0, como Python


def _dias_semana(texto: str) -> list[int]:
    """'lun,mar, Miércoles' -> [0, 1, 2]. Vacío o sin días válidos -> todos los días."""
    import unicodedata
    dias = []
    for parte in (texto or "").split(","):
        p = "".join(c for c in unicodedata.normalize("NFKD", parte.strip().lower())
                    if not unicodedata.combining(c))[:3]
        if p in DIAS_SEMANA and DIAS_SEMANA.index(p) not in dias:
            dias.append(DIAS_SEMANA.index(p))
    return sorted(dias) or list(range(7))


def _fecha(nombre: str):
    try:
        return datetime.strptime(os.environ.get(nombre, "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def leer_config() -> dict:
    """QUÉ HACE: lee los valores que configura el cliente (con valores seguros por defecto)."""
    return {
        "hora": _entero("PIPELINE_HORA", 2, 0, 23),
        "cada_dias": _entero("PIPELINE_CADA_DIAS", 1, 0, 365),
        "firecrawl_cada_dias": _entero("PIPELINE_FIRECRAWL_CADA_DIAS", 14, 0, 365),
        "apify": os.environ.get("PIPELINE_INCLUIR_APIFY", "false").strip().lower() in ("true", "1", "si", "sí"),
        "dias_semana": _dias_semana(os.environ.get("PIPELINE_DIAS_SEMANA", "")),
        "desde": _fecha("PIPELINE_DESDE"),
        "hasta": _fecha("PIPELINE_HASTA"),
    }


def toca_hoy(cfg: dict, hoy) -> bool:
    """QUÉ HACE: True si la fecha de hoy (Colombia) cumple el día de la semana y el rango."""
    if hoy.weekday() not in cfg["dias_semana"]:
        return False
    if cfg["desde"] and hoy < cfg["desde"]:
        return False
    if cfg["hasta"] and hoy > cfg["hasta"]:
        return False
    return True


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
            grupo = fase if fase in ("gemini", "eventos") else None
            pasos.append({"tipo": "script", "nombre": nombre, "carpeta": carpeta,
                          "script": script, "args": args, "grupo": grupo})
    return pasos


def fin_de_grupo(pasos: list[dict], i: int) -> int:
    """QUÉ HACE: índice del primer paso que ya NO pertenece al grupo de tramos del paso i."""
    g = pasos[i].get("grupo")
    j = i + 1
    while g and j < len(pasos) and pasos[j].get("grupo") == g:
        j += 1
    return j


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


def clave_msg(m: dict) -> str:
    """Identifica un mensaje de forma única: reanudación | paso | tanda de Silver."""
    return f"{m.get('r', 0)}|{m['i']}|{m.get('c', 0)}|{m.get('desde', 0)}"


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


def correr_silver_tanda(c_idx: int, desde: int,
                        modificados_desde: datetime | None = None) -> tuple[bool, str, int | None, int]:
    """
    QUÉ HACE: procesa UNA tanda de un contenedor (solo lo nuevo si hay 'modificados_desde').
    Devuelve (ok, detalle, siguiente_contenedor, siguiente_desde); siguiente_contenedor
    es None cuando ya no quedan contenedores.
    """
    from ingestion_silver.function_silver import procesar_tanda
    cont = CONTENEDORES_BRONZE[c_idx]
    r = procesar_tanda(cont, desde=desde, hasta=desde + TAM_TANDA_SILVER,
                       modificados_desde=modificados_desde)
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
    # Un script puede terminar "bien" pero con datos de relleno si Groq agotó su
    # cupo (error 429). Eso se marca como fallo para que se vea en el estado.
    limite_ia = ("Rate limit" in salida) or ("rate_limit_exceeded" in salida)
    if limite_ia:
        salida = "[LÍMITE DE GROQ ALCANZADO: revisar este paso]\n" + salida
    return r.returncode == 0 and not limite_ia, salida


# ─────────────────────────────────────────────────────────────
# ARRANQUE DE UNA CORRIDA
# ─────────────────────────────────────────────────────────────

def iniciar_corrida(apify: bool, firecrawl: bool, fases: list[str], forzar: bool,
                    origen: str, silver_completo: bool = False) -> tuple[dict, str | None]:
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
                   pasos_con_error=0, errores="", reanudaciones=0, reanudaciones_paso=0,
                   ultimo_avance=_ahora(), **extra)
    m0 = {"corrida": corrida, "i": 0, "r": 0, "apify": apify, "firecrawl": firecrawl, "fases": fases,
          "silver_completo": silver_completo}
    msg = json.dumps(m0)
    guardar_estado(msg_actual=msg, clave_actual=clave_msg(m0))
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
        silver_completo=req.params.get("silver_completo", "0") == "1",
    )
    if msg:
        siguiente.set(msg)
    return func.HttpResponse(json.dumps(resp, ensure_ascii=False, indent=2),
                             status_code=202 if msg else 409, mimetype="application/json")


def _minutos_desde(texto: str | None) -> float | None:
    try:
        t = datetime.strptime(texto, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


def vigilar_corrida(est: dict | None) -> str | None:
    """
    QUÉ HACE: si la corrida en curso lleva más de MINUTOS_SIN_AVANCE sin avanzar,
    devuelve el mensaje para reanudarla (o None si todo está bien).
    POR QUÉ: Azure puede reciclar el servidor a mitad de un paso; ese paso se pierde
    y, sin esto, la corrida quedaría "en_curso" para siempre y el tablero sin actualizar.
    """
    if not est or est.get("estado") != "en_curso" or not est.get("msg_actual"):
        return None
    minutos = _minutos_desde(est.get("ultimo_avance") or est.get("inicio"))
    if minutos is None or minutos < MINUTOS_SIN_AVANCE:
        return None

    corrida = est.get("corrida")
    total = int(est.get("reanudaciones", 0) or 0) + 1
    if total > MAX_REANUDACIONES_CORRIDA:
        guardar_estado(estado="interrumpida", fin=_ahora(), clave_actual="",
                       errores=((est.get("errores") or "") + "Corrida interrumpida: se perdió el "
                                f"servidor {total - 1} veces; revisar Application Insights. ")[-2000:])
        registrar_paso(corrida, 999999, "Vigilante: corrida interrumpida", False,
                       f"Se reanudó {total - 1} veces sin poder terminar.", 0)
        log.error("Vigilante: corrida %s interrumpida tras %s reanudaciones.", corrida, total - 1)
        return None

    m = json.loads(est["msg_actual"])
    pasos = armar_pasos(m.get("apify", False), m.get("firecrawl", True), m.get("fases", FASES))
    i = m["i"]
    del_paso = int(est.get("reanudaciones_paso", 0) or 0) + 1
    con_error = int(est.get("pasos_con_error", 0) or 0)
    errores = est.get("errores", "") or ""
    nombre = est.get("nombre_paso") or (pasos[i]["nombre"] if i < len(pasos) else "")

    if del_paso > MAX_REANUDAR_PASO:
        # El mismo paso se perdió varias veces: se marca fallido y se sigue con el próximo.
        con_error += 1
        errores = (errores + f"{nombre} (saltado: se perdió {del_paso - 1} veces); ")[-2000:]
        registrar_paso(corrida, i * 1000 + 999, f"{nombre} [saltado por el vigilante]", False,
                       f"El paso se perdió {del_paso - 1} veces; se continúa con el siguiente.", 0)
        m = {k: v for k, v in m.items() if k not in ("c", "desde", "sil_desde", "sil_err")}
        m["i"] = i + 1
        del_paso = 0
        if m["i"] >= len(pasos):
            guardar_estado(paso_actual=len(pasos), nombre_paso="", pasos_con_error=con_error,
                           errores=errores, fin=_ahora(), clave_actual="", reanudaciones=total,
                           estado="completada con errores")
            return None
        nombre = pasos[m["i"]]["nombre"]
    else:
        registrar_paso(corrida, i * 1000 + 990 + del_paso, f"Vigilante: reanudando {nombre}", False,
                       f"Sin avance hace {minutos:.0f} min; intento {del_paso} de {MAX_REANUDAR_PASO}.", 0)

    m["r"] = total                          # los mensajes viejos de esta corrida quedan sin efecto
    msg = json.dumps(m)
    guardar_estado(reanudaciones=total, reanudaciones_paso=del_paso, paso_actual=m["i"],
                   nombre_paso=nombre, pasos_con_error=con_error, errores=errores,
                   ultimo_avance=_ahora(), msg_actual=msg, clave_actual=clave_msg(m))
    log.warning("Vigilante: corrida %s reanudada en el paso %s (%s).", corrida, m["i"] + 1, nombre)
    return msg


@bp.timer_trigger(schedule="0 */15 * * * *", arg_name="timer", run_on_startup=False)
@bp.queue_output(arg_name="siguiente", queue_name=COLA, connection=CONN_COLA)
def pipeline_programado(timer: func.TimerRequest, siguiente: func.Out[str]) -> None:
    """
    Cada 15 minutos:
      1) VIGILANTE: si hay una corrida en curso que dejó de avanzar, la reanuda.
      2) Revisa si toca arrancar la corrida del día (PIPELINE_HORA, PIPELINE_CADA_DIAS).
    (Revisar no cuesta nada; solo arranca la corrida a la hora configurada.)
    """
    try:
        msg = vigilar_corrida(leer_estado())
    except Exception as ex:                 # noqa: BLE001
        log.warning("Vigilante: no se pudo revisar la corrida: %s", ex)
        msg = None
    if msg:
        siguiente.set(msg)
        return
    cfg = leer_config()
    if cfg["cada_dias"] == 0:
        return                                          # pausado por configuración
    ahora = hoy_colombia()
    if ahora.hour != cfg["hora"]:
        return
    if not toca_hoy(cfg, ahora.date()):
        return                                          # hoy no es día de corrida
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
    # Solo se ejecuta el mensaje que el estado espera. Así un mensaje viejo (reintento
    # de Azure después de una reanudación del vigilante) no duplica pasos.
    if est.get("clave_actual") and est["clave_actual"] != clave_msg(m):
        log.info("Mensaje %s no es el esperado (%s); se descarta.", clave_msg(m), est["clave_actual"])
        return
    # Latido: el paso empezó. El vigilante mide el tiempo sin avance desde aquí.
    guardar_estado(ultimo_avance=_ahora())

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
            # SILVER INCREMENTAL: al empezar la fase se fija desde qué fecha procesar
            # (última corrida de Silver sin errores, menos un margen). Sin fecha previa,
            # o si se pidió silver_completo=1, se procesa toda la historia.
            if "sil_desde" not in m:
                ultima = est.get("ultima_silver") or ""
                if ultima and not m.get("silver_completo"):
                    try:
                        f0 = datetime.strptime(ultima, "%Y-%m-%d %H:%M:%S UTC")
                        m["sil_desde"] = (f0 - timedelta(days=MARGEN_SILVER_DIAS)).strftime("%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        m["sil_desde"] = ""
                else:
                    m["sil_desde"] = ""
                m["sil_err"] = 0
            modificados_desde = (datetime.strptime(m["sil_desde"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                                 if m["sil_desde"] else None)
            modo = f"desde {m['sil_desde'][:10]}" if m["sil_desde"] else "completo"
            nombre = f"Silver {CONTENEDORES_BRONZE[c_idx]} {desde}-{desde + TAM_TANDA_SILVER} ({modo})"
            ok, detalle, sig_c, sig_desde = correr_silver_tanda(c_idx, desde, modificados_desde)
            if not ok:
                m["sil_err"] = m.get("sil_err", 0) + 1
            if sig_c is not None:           # quedan tandas: el siguiente mensaje sigue en Silver
                sig = {**m, "c": sig_c, "desde": sig_desde}
            elif m.get("sil_err", 0) == 0:
                # Silver terminó sin errores: la próxima corrida solo procesará lo nuevo
                guardar_estado(ultima_silver=est.get("inicio") or _ahora())
        else:
            ok, detalle = correr_script(paso["carpeta"], paso["script"], paso["args"])
            if paso.get("grupo") and any(t in detalle for t in FIN_DE_GRUPO):
                j = fin_de_grupo(pasos, i)
                if j > i + 1:
                    detalle = (f"[Se saltan {j - i - 1} tramos restantes de esta fase: "
                               f"no queda nada pendiente o se agotó la cuota del día]\n") + detalle
                    sig = {k: v for k, v in m.items() if k not in ("c", "desde", "sil_desde", "sil_err")}
                    sig["i"] = j
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
        sig = {k: v for k, v in m.items() if k not in ("c", "desde", "sil_desde", "sil_err")}
        sig["i"] = i + 1

    if sig["i"] < len(pasos):
        msg = json.dumps(sig)
        cambio_paso = sig["i"] != i
        guardar_estado(paso_actual=sig["i"], nombre_paso=pasos[sig["i"]]["nombre"],
                       pasos_con_error=con_error, errores=errores, ultimo_avance=_ahora(),
                       msg_actual=msg, clave_actual=clave_msg(sig),
                       **({"reanudaciones_paso": 0} if cambio_paso else {}))
        siguiente.set(msg)
    else:
        guardar_estado(paso_actual=len(pasos), nombre_paso="", pasos_con_error=con_error,
                       errores=errores, fin=_ahora(), clave_actual="", ultimo_avance=_ahora(),
                       estado="completada" if con_error == 0 else "completada con errores")
        log.info("Corrida %s terminada (%s pasos con error).", corrida, con_error)


def _hora_colombia(texto: str | None) -> str:
    """'2026-10-06 07:00:00 UTC' -> '06/10/2026 2:00 a. m.' (hora de Colombia)."""
    try:
        t = datetime.strptime(str(texto), "%Y-%m-%d %H:%M:%S UTC") + timedelta(hours=UTC_COLOMBIA)
    except (TypeError, ValueError):
        return ""
    h12 = t.hour % 12 or 12
    return f"{t:%d/%m/%Y} {h12}:{t:%M} {'a. m.' if t.hour < 12 else 'p. m.'}"


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
        "dias_semana": ("todos" if len(cfg["dias_semana"]) == 7 else
                        ",".join(DIAS_SEMANA[d] for d in cfg["dias_semana"])),
        "desde": cfg["desde"].isoformat() if cfg["desde"] else "sin límite",
        "hasta": cfg["hasta"].isoformat() if cfg["hasta"] else "sin límite",
        "corre_hoy": cfg["cada_dias"] > 0 and toca_hoy(cfg, hoy_colombia().date()),
    }
    if not est:
        return func.HttpResponse(json.dumps({"estado": "sin corridas", "configuracion": config_txt},
                                            ensure_ascii=False, indent=2), mimetype="application/json")
    est = {k: v for k, v in est.items()
           if k not in ("PartitionKey", "RowKey", "msg_actual", "clave_actual")}
    # Las horas se guardan en UTC (horario de Azure); se agregan en hora de Colombia
    # para que se lean sin hacer cuentas: 07:00 UTC = 2:00 a. m. en Colombia.
    for campo in ("inicio", "fin", "ultimo_avance"):
        est[f"{campo}_colombia"] = _hora_colombia(est.get(campo))
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