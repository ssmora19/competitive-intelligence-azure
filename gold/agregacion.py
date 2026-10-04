# -*- coding: utf-8 -*-
"""
agregacion.py
=============

QUÉ HACE
--------
Es el motor de agregación cuantitativa de Gold (los indicadores "Número de…").
Para un contenedor, lee los registros relevantes de Silver, los carga en un
DataFrame de pandas y calcula cada indicador SEGÚN SU NATURALEZA TEMPORAL:

  - tendencia / caducidad_blanda → serie por periodo (trimestre) + variación %
  - estado                       → nivel vigente actual (stock, no flujo)
  - vencimiento                  → conteo informativo (la urgencia va en alertas)

PARA QUÉ SIRVE
--------------
Produce las filas que alimentan la tabla goldIndicadores (salida cuantitativa
para Gerencia). NO usa IA: es pandas + la parametrización de config_gold. La
lógica es totalmente tuya y auditable — cada número se puede rastrear hasta los
registros de Silver que lo componen.

DECISIONES DE DISEÑO (defendibles)
----------------------------------
- Agrega por 'fecha_evento' (cuándo ocurrió el hecho), NO por fecha de ingesta.
  Así, de un solo backfill se obtiene una serie histórica de varios trimestres.
- Los registros sin fecha van a un cubo visible 'sin_fecha' (no se ocultan ni se
  botan): la completitud incompleta debe ser visible, no barrida bajo la alfombra.
- Separación de responsabilidades: este módulo SOLO calcula y devuelve; escribir
  en Azure es tarea de gold_data.py, invocado por el orquestador.

DEPENDENCIA
-----------
    pip install pandas
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

import config_gold as cfg

log = logging.getLogger("gold.agregacion")

SIN_FECHA = "sin_fecha"


# ---------------------------------------------------------------------------
# UTILIDADES DE PERIODO
# ---------------------------------------------------------------------------
def _parse_fecha(valor) -> datetime | None:
    """QUÉ HACE: convierte un valor de fecha (ISO, date-only, con 'Z') a datetime.
    PARA QUÉ SIRVE: normaliza 'fecha_evento' de Silver, que puede venir en varios
    formatos. Devuelve None si no se puede interpretar (→ cubo 'sin_fecha')."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor
    s = str(valor).strip().replace("Z", "+00:00")
    for intento in (s, s[:10]):  # ISO completo, luego solo la fecha
        try:
            return datetime.fromisoformat(intento)
        except (ValueError, TypeError):
            continue
    return None


def _periodo(fecha: datetime | None, granularidad: str = "trimestral") -> str:
    """QUÉ HACE: etiqueta la fecha con su periodo ('2026-Q3' o '2026-08').
    PARA QUÉ SIRVE: es la clave con la que se agrupan y comparan los conteos.
    Un cubo es tan ancho como diga 'granularidad'."""
    if fecha is None:
        return SIN_FECHA
    if granularidad == "mensual":
        return f"{fecha.year:04d}-{fecha.month:02d}"
    # trimestral (por defecto)
    trimestre = (fecha.month - 1) // 3 + 1
    return f"{fecha.year:04d}-Q{trimestre}"


def _ordenar_periodos(periodos) -> list[str]:
    """QUÉ HACE: ordena periodos cronológicamente, dejando 'sin_fecha' al final.
    PARA QUÉ SIRVE: la variación % se calcula sobre periodos en orden; el cubo
    'sin_fecha' no participa de la comparación temporal."""
    reales = sorted(p for p in periodos if p != SIN_FECHA)
    return reales + ([SIN_FECHA] if SIN_FECHA in periodos else [])


def variacion_pct(actual: int, previo: int | None) -> float | None:
    """QUÉ HACE: calcula la variación porcentual vs el periodo anterior.
    PARA QUÉ SIRVE: convierte un conteo en señal ('subió 50%'). Devuelve None si
    no hay base de comparación (primer periodo, o periodo previo en cero)."""
    if previo is None or previo == 0:
        return None
    return round((actual - previo) / previo * 100, 1)


# ---------------------------------------------------------------------------
# CONSTRUCCIÓN DEL DATAFRAME
# ---------------------------------------------------------------------------
def _a_dataframe(registros: list[dict], granularidad: str) -> pd.DataFrame:
    """
    QUÉ HACE
        Convierte los registros de Silver en un DataFrame con las columnas que el
        motor necesita, incluyendo el 'periodo' ya calculado por fecha_evento.
        Descarta eventos anteriores a config_gold.ANIO_MINIMO (fechas muy viejas
        que ensucian las tendencias), pero conserva los que no tienen fecha.

    PARA QUÉ SIRVE
        Deja los datos en forma tabular para que las agregaciones sean groupby de
        pandas — probados y rápidos — en vez de bucles a mano.
    """
    anio_min = getattr(cfg, "ANIO_MINIMO", 0)
    anio_max = datetime.now(timezone.utc).year  # nada del futuro (fechas inválidas)
    filas = []
    descartados_viejos = 0
    descartados_futuros = 0
    for r in registros:
        fecha = _parse_fecha(r.get("fecha_evento"))
        # descartar eventos demasiado viejos (pero conservar los sin fecha)
        if fecha is not None and anio_min and fecha.year < anio_min:
            descartados_viejos += 1
            continue
        # descartar fechas futuras (errores de parseo: 2029, etc.)
        if fecha is not None and fecha.year > anio_max:
            descartados_futuros += 1
            continue
        atributos = r.get("atributos") or {}
        if not isinstance(atributos, dict):
            atributos = {}
        filas.append({
            "id_normalizado": r.get("id_normalizado", ""),
            "entidad": (r.get("entidad") or "").strip() or "sin_entidad",
            "tipo_senal": (r.get("tipo_senal") or "").strip(),
            "relevancia": _num_relevancia(r.get("relevancia")),
            "periodo": _periodo(fecha, granularidad),
            "fecha_cierre": r.get("fecha_cierre"),
            "temas": r.get("temas") or [],
            "atributos": atributos,
        })
    if descartados_viejos or descartados_futuros:
        log.info("Agregación: %d eventos descartados por viejos (< %s) y %d por fecha futura (> %s).",
                 descartados_viejos, anio_min, descartados_futuros, anio_max)
    return pd.DataFrame(filas)


def _num_relevancia(valor) -> float:
    """QUÉ HACE: extrae la relevancia como número desde el formato real de Silver.
    PARA QUÉ SIRVE: Silver guarda relevancia como {'score': 0-1, 'pasa_umbral':..};
    esta función saca el score, tolerando también un número plano."""
    if isinstance(valor, dict):
        return _num(valor.get("score"))
    return _num(valor)


def _num(valor) -> float:
    """QUÉ HACE: convierte relevancia a número de forma tolerante.
    PARA QUÉ SIRVE: Silver puede guardar relevancia como '3', 3, True…; para
    promediar necesitamos un número. Lo no interpretable se trata como 0."""
    if isinstance(valor, bool):
        return 1.0 if valor else 0.0
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# AGREGACIONES POR NATURALEZA
# ---------------------------------------------------------------------------
def _agregar_tendencia(df: pd.DataFrame, ind_cfg: dict) -> list[dict]:
    """
    QUÉ HACE
        Cuenta una señal de tendencia por periodo, con desglose por entidad, y
        calcula la variación % de cada periodo contra el anterior.

    PARA QUÉ SIRVE
        Es el caso mayoritario (lanzamientos, alianzas, regulaciones…). Produce
        una fila por periodo, lista para goldIndicadores, con la serie completa.

    DEVUELVE
        Lista de dicts: {"periodo", "valor", "variacion_pct", "detalle"}.
    """
    tipo = ind_cfg.get("tipo_senal")
    sub = df[df["tipo_senal"] == tipo] if tipo else df
    if sub.empty:
        return []

    # Conteo total por periodo
    por_periodo = sub.groupby("periodo").size().to_dict()
    # Desglose por entidad dentro de cada periodo
    por_entidad = (
        sub.groupby(["periodo", "entidad"]).size()
        .reset_index(name="n")
    )

    filas = []
    orden = _ordenar_periodos(por_periodo.keys())
    previo = None
    for p in orden:
        valor = int(por_periodo.get(p, 0))
        # 'sin_fecha' no arrastra variación (no es comparable temporalmente)
        var = None if p == SIN_FECHA else variacion_pct(valor, previo)
        desglose = {
            row["entidad"]: int(row["n"])
            for _, row in por_entidad[por_entidad["periodo"] == p].iterrows()
        }
        filas.append({
            "periodo": p,
            "valor": valor,
            "variacion_pct": var,
            "detalle": {"por_entidad": desglose},
        })
        if p != SIN_FECHA:
            previo = valor
    return filas


def _agregar_estado(df: pd.DataFrame, ind_cfg: dict) -> list[dict]:
    """
    QUÉ HACE
        Calcula el NIVEL VIGENTE de una señal de estado (stock), no un flujo por
        periodo. Soporta varios modos: conteo de entidades, agrupación por un
        atributo, brecha vs stack de TAK, o conteo simple de un tipo vigente.

    PARA QUÉ SIRVE
        Para señales que "son ciertas ahora" (contratos vigentes, distribución
        por segmento, brecha de capacidad). El periodo se etiqueta 'vigente'.
    """
    modo = ind_cfg.get("modo")
    tipo = ind_cfg.get("tipo_senal")

    if modo == "conteo_entidades":
        valor = int(df["entidad"].nunique())
        detalle = {"entidades": sorted(df["entidad"].unique().tolist())[:50]}

    elif modo == "agrupacion":
        campo = ind_cfg.get("campo", "")
        # el atributo puede estar en la columna 'atributos' (dict)
        valores = df["atributos"].apply(lambda a: a.get(campo, "sin_dato"))
        conteo = valores.value_counts().to_dict()
        valor = int(sum(conteo.values()))
        detalle = {f"por_{campo}": {str(k): int(v) for k, v in conteo.items()}}

    elif modo == "brecha_stack":
        sub = df[df["tipo_senal"] == tipo] if tipo else df
        # brecha = señales cuya tecnología NO está en el stack de TAK
        def _es_brecha(row) -> bool:
            texto = " ".join(str(t) for t in row["temas"]).lower()
            return not any(s in texto for s in cfg.STACK_TAK)
        brechas = sub[sub.apply(_es_brecha, axis=1)] if not sub.empty else sub
        valor = int(len(brechas))
        detalle = {"total_demanda": int(len(sub)), "en_brecha": valor}

    else:  # conteo simple de un tipo vigente (ej. contrato_vigente)
        sub = df[df["tipo_senal"] == tipo] if tipo else df
        valor = int(len(sub))
        detalle = {"por_entidad": sub.groupby("entidad").size().to_dict()}

    return [{"periodo": "vigente", "valor": valor, "variacion_pct": None, "detalle": detalle}]


def _agregar_vencimiento(df: pd.DataFrame, ind_cfg: dict) -> list[dict]:
    """
    QUÉ HACE
        Cuenta cuántas señales de vencimiento están ACTUALMENTE ABIERTAS (fecha de
        cierre en el futuro) y cuántas ya vencieron. NO consolida por trimestre.

    PARA QUÉ SIRVE
        Da a Gerencia el dato informativo ("hay N licitaciones abiertas"), mientras
        que la priorización por urgencia (días restantes) la produce el motor de
        alertas/scoring. Aquí solo se cuenta el estado abierto/vencido.
    """
    tipo = ind_cfg.get("tipo_senal")
    sub = df[df["tipo_senal"] == tipo] if tipo else df
    ahora = datetime.now(timezone.utc)
    abiertas, vencidas, sin_fecha = 0, 0, 0
    for _, row in sub.iterrows():
        fc = _parse_fecha(row.get("fecha_cierre"))
        if fc is None:
            sin_fecha += 1
        elif fc.replace(tzinfo=fc.tzinfo or timezone.utc) >= ahora:
            abiertas += 1
        else:
            vencidas += 1
    return [{
        "periodo": "vigente",
        "valor": abiertas,
        "variacion_pct": None,
        "detalle": {"abiertas": abiertas, "vencidas": vencidas, "sin_fecha_cierre": sin_fecha},
    }]


# ---------------------------------------------------------------------------
# ORQUESTACIÓN A NIVEL CONTENEDOR
# ---------------------------------------------------------------------------
def calcular_indicadores(registros: list[dict], contenedor: str,
                         granularidad: str = "trimestral") -> dict:
    """
    QUÉ HACE
        Calcula TODOS los indicadores alimentables de un contenedor a partir de
        sus registros de Silver, enrutando cada uno según su naturaleza temporal.

    PARA QUÉ SIRVE
        Es la función pública que usa el orquestador (calcular_gold.py). Devuelve
        una estructura lista para escribir en goldIndicadores y un resumen para el
        log de ejecución.

    PARÁMETROS
        registros    : lista de dicts de Silver (de gold_data.leer_silver()).
        contenedor   : nombre del contenedor (ej. 'competidores').
        granularidad : 'trimestral' (def.) o 'mensual'.

    DEVUELVE
        {
          "filas": [ {indicador, periodo, valor, variacion_pct, kin_id, desc, detalle}, ... ],
          "resumen": {indicador: n_periodos, ...},
        }
    """
    c = cfg.config(contenedor)
    kin_id = c["kin_id"]
    df = _a_dataframe(registros, granularidad)

    filas: list[dict] = []
    resumen: dict[str, int] = {}

    for nombre, ind_cfg in cfg.indicadores_alimentables(contenedor).items():
        # Indicadores derivados del scoring (ej. n_oportunidades_captura) no se
        # calculan aquí: los produce scoring.py. Se marcan y se saltan.
        if ind_cfg.get("modo") == "scoring":
            resumen[nombre] = 0
            continue

        naturaleza = ind_cfg.get("naturaleza", "tendencia")
        if naturaleza in ("tendencia", "caducidad_blanda"):
            resultado = _agregar_tendencia(df, ind_cfg)
        elif naturaleza == "estado":
            resultado = _agregar_estado(df, ind_cfg)
        elif naturaleza == "vencimiento":
            resultado = _agregar_vencimiento(df, ind_cfg)
        else:
            log.warning("Naturaleza desconocida '%s' en %s.%s; trato como tendencia.",
                        naturaleza, contenedor, nombre)
            resultado = _agregar_tendencia(df, ind_cfg)

        for fila in resultado:
            filas.append({
                "indicador": nombre,
                "periodo": fila["periodo"],
                "valor": fila["valor"],
                "variacion_pct": fila["variacion_pct"],
                "kin_id": kin_id,
                "desc": ind_cfg.get("desc", ""),
                "naturaleza": naturaleza,
                "detalle": fila["detalle"],
            })
        resumen[nombre] = len(resultado)

    log.info("Agregación '%s': %d filas de indicador sobre %d registros.",
             contenedor, len(filas), len(registros))
    return {"filas": filas, "resumen": resumen}


# ===========================================================================
# PRUEBA AUTÓNOMA (corre sin Azure, con datos sintéticos)
#   Ejecuta:  python agregacion.py
# ===========================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Datos falsos que imitan registros de Silver de 'competidores' en 3 trimestres
    registros_demo = [
        # Q1: 2 lanzamientos
        {"id_normalizado": "1", "entidad": "CompetidorA", "tipo_senal": "lanzamiento",
         "relevancia": 4, "fecha_evento": "2026-01-15"},
        {"id_normalizado": "2", "entidad": "CompetidorB", "tipo_senal": "lanzamiento",
         "relevancia": 3, "fecha_evento": "2026-02-20"},
        # Q2: 3 lanzamientos (subida)
        {"id_normalizado": "3", "entidad": "CompetidorA", "tipo_senal": "lanzamiento",
         "relevancia": 5, "fecha_evento": "2026-04-10"},
        {"id_normalizado": "4", "entidad": "CompetidorA", "tipo_senal": "lanzamiento",
         "relevancia": 4, "fecha_evento": "2026-05-01"},
        {"id_normalizado": "5", "entidad": "CompetidorB", "tipo_senal": "lanzamiento",
         "relevancia": 3, "fecha_evento": "2026-06-30"},
        # Q2: 1 alianza
        {"id_normalizado": "6", "entidad": "CompetidorA", "tipo_senal": "alianza",
         "relevancia": 5, "fecha_evento": "2026-05-15"},
        # Un registro sin fecha (debe ir a cubo 'sin_fecha')
        {"id_normalizado": "7", "entidad": "CompetidorC", "tipo_senal": "lanzamiento",
         "relevancia": 2, "fecha_evento": None},
    ]

    print("\n=== PRUEBA: agregación de 'competidores' (trimestral) ===\n")
    resultado = calcular_indicadores(registros_demo, "competidores", "trimestral")

    for fila in resultado["filas"]:
        var = f"{fila['variacion_pct']:+.1f}%" if fila["variacion_pct"] is not None else "  n/a "
        det = fila["detalle"].get("por_entidad", {})
        print(f"  {fila['indicador']:22} {fila['periodo']:10} valor={fila['valor']:<3} "
              f"var={var:>8}   {det}")

    print("\n  Interpretación esperada:")
    print("   - n_lanzamientos Q1=2, Q2=3 → variación +50.0% (señal de que subió)")
    print("   - el registro sin fecha aparece en cubo 'sin_fecha', visible, sin variación")
    print("   - n_alianzas solo en Q2=1\n")