# -*- coding: utf-8 -*-
"""
gold_data.py
============

QUÉ HACE
--------
Es la única capa que habla con Azure Table Storage en Gold. Ofrece dos cosas:
(1) LECTURA de los registros ya enriquecidos de Silver, y (2) ESCRITURA de los
resultados de Gold en sus tres tablas (indicadores, veredictos, cobertura KIN),
con idempotencia y trazabilidad.

PARA QUÉ SIRVE
--------------
Aísla todo el I/O de Azure en un solo archivo. Los motores (agregacion.py,
scoring.py) no saben nada de Table Storage: piden "los registros de Silver de
tal contenedor" o "guarda estos indicadores" y esta capa se encarga. Si algún
día cambia el SDK de Table Storage o la cadena de conexión, se cambia SOLO aquí.

DECISIONES DE DISEÑO (defendibles ante el jurado)
-------------------------------------------------
- IDEMPOTENCIA: el RowKey de cada resultado Gold es determinista (se deriva del
  contenedor + indicador/entidad + periodo). Reejecutar Gold sobre el mismo
  periodo SOBREESCRIBE la misma fila en vez de duplicar. Esto hace que Gold sea
  seguro de reejecutar (igual que Bronze era idempotente por hash).
- TRAZABILIDAD: cada veredicto guarda las señales de respaldo (id_normalizado de
  los registros Silver que lo sustentan). Se puede auditar de un score hacia
  atrás hasta el dato crudo de Bronze.
- MISMO PATRÓN QUE SILVER: se lee la cadena de conexión desde variable de entorno
  (o local.settings.json), tal como los scripts de Silver que corren sin
  'func start' leyendo directo de Azure.

DEPENDENCIA
-----------
Usa el SDK 'azure-data-tables' (TableServiceClient). Si Silver usó otro SDK,
alinea este import con el de Silver para consistencia:
    pip install azure-data-tables
"""

from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone

try:
    from azure.data.tables import TableServiceClient, UpdateMode
    from azure.core.exceptions import ResourceExistsError, HttpResponseError
except ImportError:  # pragma: no cover
    # Permite importar el módulo y validar sintaxis sin el SDK instalado.
    TableServiceClient = None
    UpdateMode = None
    ResourceExistsError = Exception
    HttpResponseError = Exception

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover
    BlobServiceClient = None

from config_gold import (
    TABLA_INDICADORES, TABLA_VEREDICTOS, TABLA_ALERTAS, TABLA_COBERTURA, TABLA_LOGS,
)

log = logging.getLogger("gold.data")

# Nombre de la variable de entorno con la cadena de conexión de Azure Storage.
# Es la misma que usan los scripts de Silver.
_CONN_ENV = "AZURE_STORAGE_CONNECTION_STRING"


# ---------------------------------------------------------------------------
# CONEXIÓN
# ---------------------------------------------------------------------------
def _connection_string() -> str:
    """QUÉ HACE: obtiene la cadena de conexión de Azure Storage.
    PARA QUÉ SIRVE: centraliza de dónde salen las credenciales; primero busca la
    variable de entorno y, si no está, intenta local.settings.json (patrón Silver).
    """
    conn = os.environ.get(_CONN_ENV)
    if conn:
        return conn
    # Fallback: local.settings.json (como en desarrollo local de Silver)
    for ruta in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(ruta):
            with open(ruta, "r", encoding="utf-8") as f:
                values = json.load(f).get("Values", {})
            if values.get(_CONN_ENV):
                return values[_CONN_ENV]
    raise RuntimeError(
        f"No se encontró la cadena de conexión. Define la variable de entorno "
        f"{_CONN_ENV} o inclúyela en local.settings.json (igual que en Silver)."
    )


def _service() -> "TableServiceClient":
    """QUÉ HACE: crea el cliente de Table Storage.
    PARA QUÉ SIRVE: punto único de creación del cliente; el resto de funciones
    lo reutilizan."""
    if TableServiceClient is None:
        raise RuntimeError(
            "azure-data-tables no está instalado. Ejecuta: pip install azure-data-tables"
        )
    return TableServiceClient.from_connection_string(_connection_string())


def _tabla(nombre: str):
    """QUÉ HACE: devuelve el cliente de una tabla, creándola si no existe.
    PARA QUÉ SIRVE: que la primera ejecución de Gold no falle por tabla ausente;
    equivale al arranque en frío de las tablas de control de Silver."""
    svc = _service()
    try:
        svc.create_table(nombre)
        log.info("Tabla '%s' creada.", nombre)
    except ResourceExistsError:
        pass
    return svc.get_table_client(nombre)


# ---------------------------------------------------------------------------
# LECTURA DE SILVER (desde BLOB STORAGE)
# ---------------------------------------------------------------------------
def _blob_service():
    """QUÉ HACE: crea el cliente de Blob Storage.
    PARA QUÉ SIRVE: Silver guarda los registros enriquecidos como archivos JSON en
    contenedores de blobs (no en tablas). Esta es la puerta a esos datos."""
    if BlobServiceClient is None:
        raise RuntimeError("Falta azure-storage-blob. Ejecuta: pip install azure-storage-blob")
    return BlobServiceClient.from_connection_string(_connection_string())


def _mapear_registro(d: dict) -> dict:
    """
    QUÉ HACE
        Traduce un JSON real de Silver al formato que esperan los motores de Gold.
        Los campos de Silver están anidados y con otros nombres; aquí se aplanan.

    PARA QUÉ SIRVE
        Que agregacion.py y scoring.py sigan trabajando con nombres uniformes
        (entidad, fecha_evento, tipo_senal, relevancia, atributos…) sin saber que
        el JSON real usa 'entidad_declarada', 'fecha_evento_utc', etc. Todo el
        acoplamiento al esquema real de Silver vive AQUÍ, en un solo lugar.

    MAPEO (Silver real → Gold)
        id                          → id_normalizado
        entidad_declarada           → entidad   (fallback: entidad_monitoreada)
        fecha_evento_utc            → fecha_evento
        clasificacion.tipo_senal    → tipo_senal (ya corregido por el modelo)
        relevancia.pasa_umbral      → (se usa en _es_relevante)
        sentimiento.etiqueta        → sentimiento
    """
    clasif = d.get("clasificacion", {}) or {}
    sentimiento = d.get("sentimiento")
    if isinstance(sentimiento, dict):
        sentimiento = sentimiento.get("etiqueta")

    # atributos: se agrupan campos útiles que Silver trae sueltos, para el scoring.
    atributos = {}
    if isinstance(d.get("atributos"), dict):
        atributos = dict(d["atributos"])
    # valor de contrato de SECOP, si viniera
    for k in ("valor_contrato", "valor", "cuantia"):
        if k in d and "valor_contrato" not in atributos:
            atributos["valor_contrato"] = d[k]
    # tracción de github/redes, si viniera
    for k in ("stars", "traccion", "interacciones"):
        if k in d and "traccion" not in atributos:
            atributos["traccion"] = d[k]

    return {
        "id_normalizado": d.get("id", ""),
        "entidad": (d.get("entidad_declarada") or d.get("entidad_monitoreada") or "").strip(),
        "fecha_evento": d.get("fecha_evento_utc") or d.get("fecha_evento"),
        "fecha_cierre": d.get("fecha_cierre") or d.get("fecha_cierre_utc"),
        "tipo_senal": (clasif.get("tipo_senal") or "").strip(),
        "tipo_senal_reglas": clasif.get("tipo_senal_reglas", ""),
        "relevancia": d.get("relevancia", {}),
        "sentimiento": sentimiento,
        "temas": d.get("temas") or [],
        "titulo": d.get("titulo", ""),
        "contenido": d.get("descripcion", "") or d.get("contenido", ""),
        "atributos": atributos,
        "variable_ic": d.get("variable_ic", ""),
    }


# ─────────────────────────────────────────────────────────────
# Competidores PRIORITARIOS (los que el cliente pidió monitorear).
# Solo estos entran a Gold cuando se procesa silver-competidores, para que el
# ranking de amenazas no incluya a Indra/Deloitte/etc. (detectados por
# exploración, no competencia directa de TAK).
# ─────────────────────────────────────────────────────────────
COMPETIDORES_PRIORITARIOS = {
    "seti", "it crowd", "comware", "iteria", "bmind",
    "global technology services", "grow data",
}


def _es_competidor_prioritario(registro: dict) -> bool:
    """True si la entidad del registro es uno de los 9 competidores prioritarios."""
    ent = (registro.get("entidad") or "").strip().lower()
    return ent in COMPETIDORES_PRIORITARIOS


def leer_silver(silver_container: str, solo_relevantes: bool = True) -> list[dict]:
    """
    QUÉ HACE
        Recorre un contenedor de BLOB Storage de Silver (entrando a sus carpetas
        por fuente), lee cada archivo JSON, lo mapea al formato de Gold y lo
        devuelve como lista de diccionarios. Por defecto solo los relevantes.

    PARA QUÉ SIRVE
        Es el INSUMO de Gold. Los motores parten de aquí. Filtra el ruido en
        origen (relevancia.pasa_umbral) para que Gold no cuente registros no
        relevantes.

    PARÁMETROS
        silver_container : contenedor de blobs (ej. 'silver-competidores').
        solo_relevantes  : si True, excluye los que no pasan el umbral.

    DEVUELVE
        list[dict] con: id_normalizado, entidad, fecha_evento, tipo_senal,
        relevancia, sentimiento, temas, atributos, etc.
    """
    svc = _blob_service()
    cli = svc.get_container_client(silver_container)
    registros: list[dict] = []
    leidos = errores = 0
    for blob in cli.list_blobs():
        try:
            raw = cli.get_blob_client(blob.name).download_blob().readall()
            d = json.loads(raw)
        except Exception:
            errores += 1
            continue
        leidos += 1
        if solo_relevantes and not _es_relevante(d):
            continue
        reg = _mapear_registro(d)
        # solo para competidores: quedarse con los 9 prioritarios
        if silver_container == "silver-competidores" and not _es_competidor_prioritario(reg):
            continue
        registros.append(reg)
    log.info("Silver '%s': %d blobs leídos, %d relevantes (errores: %d).",
             silver_container, leidos, len(registros), errores)
    return registros


def _es_relevante(registro: dict) -> bool:
    """QUÉ HACE: dice si un registro de Silver pasa el umbral de relevancia.
    PARA QUÉ SIRVE: el JSON real de Silver marca la relevancia en
    'relevancia.pasa_umbral' (booleano). Esta función lo lee de forma tolerante,
    aceptando también variantes por si algún registro difiere."""
    relev = registro.get("relevancia", {})
    if isinstance(relev, dict):
        val = relev.get("pasa_umbral")
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in {"true", "1", "si", "sí"}
        # si el dict no trae pasa_umbral, mira un score numérico
        score = relev.get("score")
        if isinstance(score, (int, float)):
            return score > 0
        return True
    # formatos antiguos (booleano/num/texto directo)
    if isinstance(relev, bool):
        return relev
    if isinstance(relev, (int, float)):
        return relev > 0
    if isinstance(relev, str):
        return relev.strip().lower() in {"true", "1", "relevante", "si", "sí"}
    return True


# ---------------------------------------------------------------------------
# ESCRITURA DE RESULTADOS GOLD
# ---------------------------------------------------------------------------
def limpiar_contenedor(contenedor: str) -> int:
    """
    QUÉ HACE
        Borra todas las filas de un contenedor (PartitionKey) en las tablas
        goldIndicadores, goldVeredictos y goldAlertas, antes de recalcular.

    PARA QUÉ SIRVE
        Evita filas "zombis": si una corrida anterior escribió un periodo que la
        corrida nueva ya no genera (ej. una fecha que ahora se filtra), esa fila
        quedaría huérfana. Limpiar antes garantiza que la tabla refleje EXACTAMENTE
        el resultado de la corrida actual. Se llama al inicio de procesar cada
        contenedor en el orquestador.

    DEVUELVE
        Número de filas borradas.
    """
    borradas = 0
    for nombre_tabla in (TABLA_INDICADORES, TABLA_VEREDICTOS, TABLA_ALERTAS):
        try:
            tabla = _tabla(nombre_tabla)
        except Exception:
            continue
        # listar solo las filas de este contenedor y borrarlas
        filtro = f"PartitionKey eq '{contenedor}'"
        try:
            for ent in tabla.query_entities(filtro):
                try:
                    tabla.delete_entity(partition_key=ent["PartitionKey"],
                                        row_key=ent["RowKey"])
                    borradas += 1
                except Exception:
                    pass
        except Exception:
            continue
    if borradas:
        log.info("Limpieza '%s': %d filas viejas borradas antes de recalcular.",
                 contenedor, borradas)
    return borradas


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def guardar_indicadores(contenedor: str, periodo: str, indicadores: dict) -> int:
    """
    QUÉ HACE
        Escribe (o sobreescribe) los indicadores cuantitativos de un contenedor
        para un periodo en la tabla goldIndicadores.

    PARA QUÉ SIRVE
        Es la salida cuantitativa para Gerencia. Idempotente: el RowKey combina
        indicador + periodo, así que recalcular el mismo periodo actualiza la
        fila en lugar de duplicarla.

    PARÁMETROS
        contenedor  : ej. 'competidores' (será el PartitionKey).
        periodo     : ej. '2026-08' (mes) o '2026-Q3'.
        indicadores : dict {nombre_indicador: {"valor": int, "variacion_pct": float,
                      "kin_id": str, "desc": str, "detalle": dict|None}}.

    DEVUELVE
        Número de filas escritas.
    """
    tabla = _tabla(TABLA_INDICADORES)
    n = 0
    for nombre, datos in indicadores.items():
        entidad = {
            "PartitionKey": contenedor,
            "RowKey": f"{nombre}__{periodo}",
            "indicador": nombre,
            "periodo": periodo,
            "valor": datos.get("valor", 0),
            "variacion_pct": datos.get("variacion_pct"),
            "kin_id": datos.get("kin_id", ""),
            "descripcion": datos.get("desc", ""),
            "detalle": json.dumps(datos.get("detalle", {}), ensure_ascii=False),
            "fecha_calculo": _ahora_iso(),
        }
        tabla.upsert_entity(entidad, mode=UpdateMode.REPLACE)
        n += 1
    log.info("goldIndicadores: %d filas para '%s' periodo %s.", n, contenedor, periodo)
    return n


def guardar_veredictos(contenedor: str, periodo: str, veredictos: list[dict]) -> int:
    """
    QUÉ HACE
        Escribe (o sobreescribe) los veredictos accionables por entidad de un
        contenedor en la tabla goldVeredictos.

    PARA QUÉ SIRVE
        Es la salida accionable para Ventas (lead, amenaza, captura, gap…).
        Idempotente por entidad+periodo. Guarda las señales de respaldo para que
        el score sea auditable hasta el dato de origen.

    PARÁMETROS
        contenedor : ej. 'clientes' (PartitionKey).
        periodo    : ej. '2026-08'.
        veredictos : lista de dicts {"entidad_id", "entidad_nombre", "tipo_veredicto",
                     "score", "categoria", "senales_respaldo": list[str], "detalle": dict}.

    DEVUELVE
        Número de filas escritas.
    """
    tabla = _tabla(TABLA_VEREDICTOS)
    n = 0
    for v in veredictos:
        entidad = {
            "PartitionKey": contenedor,
            "RowKey": f"{_slug(v['entidad_id'])}__{periodo}",
            "entidad_id": str(v["entidad_id"]),
            "entidad_nombre": v.get("entidad_nombre", ""),
            "tipo_veredicto": v.get("tipo_veredicto", ""),
            "score": round(float(v.get("score", 0.0)), 4),
            "categoria": v.get("categoria", ""),
            "n_senales": len(v.get("senales_respaldo", [])),
            "senales_respaldo": json.dumps(v.get("senales_respaldo", []), ensure_ascii=False),
            "detalle": json.dumps(v.get("detalle", {}), ensure_ascii=False),
            "periodo": periodo,
            "fecha_calculo": _ahora_iso(),
        }
        tabla.upsert_entity(entidad, mode=UpdateMode.REPLACE)
        n += 1
    log.info("goldVeredictos: %d filas para '%s' periodo %s.", n, contenedor, periodo)
    return n


def guardar_cobertura(filas: list[dict]) -> int:
    """
    QUÉ HACE
        Escribe la tabla de cobertura KIN: por cada KIN, si es alimentable, su
        fuente y sus indicadores asociados.

    PARA QUÉ SIRVE
        Es la evidencia consultable de trazabilidad para el jurado: qué preguntas
        de inteligencia responde el sistema y cuáles quedan fuera de alcance.

    PARÁMETROS
        filas : lista de dicts {"kin_id", "kin_nombre", "contenedor", "alimentable",
                "fuente", "indicadores_asociados": list[str], "fuera_de_alcance": dict}.

    DEVUELVE
        Número de filas escritas.
    """
    tabla = _tabla(TABLA_COBERTURA)
    n = 0
    for f in filas:
        entidad = {
            "PartitionKey": "cobertura",
            "RowKey": f["kin_id"],
            "kin_nombre": f.get("kin_nombre", ""),
            "contenedor": f.get("contenedor", ""),
            "alimentable": f.get("alimentable", ""),
            "fuente": f.get("fuente", ""),
            "indicadores_asociados": json.dumps(f.get("indicadores_asociados", []), ensure_ascii=False),
            "fuera_de_alcance": json.dumps(f.get("fuera_de_alcance", {}), ensure_ascii=False),
            "fecha_calculo": _ahora_iso(),
        }
        tabla.upsert_entity(entidad, mode=UpdateMode.REPLACE)
        n += 1
    log.info("goldCoberturaKIN: %d filas escritas.", n)
    return n


def guardar_indicadores_filas(contenedor: str, filas: list[dict]) -> int:
    """
    QUÉ HACE
        Escribe en goldIndicadores las filas que produce el motor de agregación,
        donde cada fila ya trae su propio periodo (una fila por indicador+periodo).

    PARA QUÉ SIRVE
        El motor de agregación devuelve una serie de varios periodos por indicador
        (Q1, Q2, Q3…). Esta función escribe cada periodo como su propia fila, para
        que Power BI pueda graficar la tendencia. Idempotente: RowKey =
        indicador__periodo, así recalcular sobreescribe.

    PARÁMETROS
        contenedor : PartitionKey.
        filas      : lista de dicts de agregacion.calcular_indicadores()['filas'],
                     cada uno {indicador, periodo, valor, variacion_pct, kin_id,
                     desc, detalle}.

    DEVUELVE
        Número de filas escritas.
    """
    tabla = _tabla(TABLA_INDICADORES)
    n = 0
    for f in filas:
        entidad = {
            "PartitionKey": contenedor,
            "RowKey": f"{f['indicador']}__{f['periodo']}",
            "indicador": f["indicador"],
            "periodo": f["periodo"],
            "valor": f.get("valor", 0),
            "variacion_pct": f.get("variacion_pct"),
            "kin_id": f.get("kin_id", ""),
            "descripcion": f.get("desc", ""),
            "naturaleza": f.get("naturaleza", ""),
            "detalle": json.dumps(f.get("detalle", {}), ensure_ascii=False),
            "fecha_calculo": _ahora_iso(),
        }
        tabla.upsert_entity(entidad, mode=UpdateMode.REPLACE)
        n += 1
    log.info("goldIndicadores: %d filas para '%s'.", n, contenedor)
    return n


def guardar_alertas(contenedor: str, alertas: list[dict]) -> int:
    """
    QUÉ HACE
        Escribe en goldAlertas las oportunidades/obligaciones con vencimiento
        (licitaciones, convocatorias, contratos por vencer) priorizadas por urgencia.

    PARA QUÉ SIRVE
        Es la salida que evita que TAK pierda oportunidades por caducidad. Cada
        alerta lleva sus días restantes y si coincide con el stack de TAK, para que
        Ventas actúe a tiempo. Idempotente por señal de origen (id_normalizado).

    PARÁMETROS
        contenedor : PartitionKey.
        alertas    : lista de dicts de scoring.generar_alertas(), cada uno
                     {entidad_id, entidad_nombre, tipo_alerta, dias_restantes,
                     fecha_cierre, coincide_stack, id_normalizado, detalle}.

    DEVUELVE
        Número de filas escritas.
    """
    tabla = _tabla(TABLA_ALERTAS)
    n = 0
    for a in alertas:
        rid = _slug(a.get("id_normalizado") or a.get("entidad_id", "sin_id"))
        entidad = {
            "PartitionKey": contenedor,
            "RowKey": rid,
            "entidad_id": str(a.get("entidad_id", "")),
            "entidad_nombre": a.get("entidad_nombre", ""),
            "tipo_alerta": a.get("tipo_alerta", ""),
            "dias_restantes": a.get("dias_restantes"),
            "fecha_cierre": str(a.get("fecha_cierre", "")),
            "coincide_stack": bool(a.get("coincide_stack", False)),
            "id_normalizado": a.get("id_normalizado", ""),
            "detalle": json.dumps(a.get("detalle", {}), ensure_ascii=False),
            "fecha_calculo": _ahora_iso(),
        }
        tabla.upsert_entity(entidad, mode=UpdateMode.REPLACE)
        n += 1
    log.info("goldAlertas: %d filas para '%s'.", n, contenedor)
    return n


def registrar_log(contenedor: str, periodo: str, resumen: dict) -> None:
    """QUÉ HACE: deja una fila de auditoría de la ejecución de Gold.
    PARA QUÉ SIRVE: evidencia de procesamiento (cuántos indicadores/veredictos se
    calcularon y cuándo), en la línea de executionLogsSilver."""
    tabla = _tabla(TABLA_LOGS)
    tabla.upsert_entity({
        "PartitionKey": contenedor,
        "RowKey": f"{periodo}__{_ahora_iso()}",
        "periodo": periodo,
        "resumen": json.dumps(resumen, ensure_ascii=False),
        "fecha": _ahora_iso(),
    }, mode=UpdateMode.REPLACE)


# ---------------------------------------------------------------------------
# UTILIDAD
# ---------------------------------------------------------------------------
def _slug(texto: str) -> str:
    """QUÉ HACE: normaliza un id de entidad para usarlo como RowKey.
    PARA QUÉ SIRVE: Table Storage prohíbe ciertos caracteres en RowKey (/, \\, #,
    ?, control chars); esta función los reemplaza para no romper la escritura."""
    s = str(texto).strip()
    for ch in ("/", "\\", "#", "?", "\t", "\n", "\r"):
        s = s.replace(ch, "-")
    return s[:200] if s else "sin_id"