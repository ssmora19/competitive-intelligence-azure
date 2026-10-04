# -*- coding: utf-8 -*-
"""
scoring.py
==========

QUÉ HACE
--------
Es el motor de scoring de Gold. Toma las señales de Silver, las agrupa por entidad
y produce dos salidas accionables:

  1. VEREDICTOS por entidad (lead caliente, amenaza alta, gap crítico…): aplica la
     fórmula de reglas ponderadas de config_gold, normaliza a un score 0–1 y lo
     mapea a una categoría según los umbrales del contenedor.
  2. ALERTAS de vencimiento (licitaciones, convocatorias, contratos por vencer):
     recorre las señales con fecha de cierre y las prioriza por urgencia (días
     restantes), para que TAK no las pierda por caducidad.

PARA QUÉ SIRVE
--------------
Es la inteligencia accionable para Ventas. A diferencia de un modelo de caja
negra, CADA score trae su desglose de factores (cuánto aportó cada uno), de modo
que se puede explicar peso por peso ante el jurado: "este cliente es lead caliente
porque el 60% de su score viene de que sus necesidades tocan el stack de TAK".

PRINCIPIOS (defendibles)
------------------------
- Reglas ponderadas transparentes, NO machine learning. Cada peso vive en la
  config con su racional, y aquí se aplica de forma explícita.
- El score se descompone: detalle['factores'] muestra valor, peso y aporte de
  cada factor. Trazabilidad total del "por qué".
- senales_respaldo lista los id_normalizado de Silver que sustentan el veredicto:
  se puede auditar de un score hacia atrás hasta el dato crudo de Bronze.
- Sin IA. La IA (Gemini) solo entra después, opcional, para redactar la
  explicación en lenguaje natural sobre estos veredictos ya calculados.

DEPENDENCIA
-----------
    pip install pandas   (indirecta, vía agregacion para el parseo de fechas)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import config_gold as cfg
from agregacion import _parse_fecha  # parser de fechas único y compartido

log = logging.getLogger("gold.scoring")


# ---------------------------------------------------------------------------
# ETIQUETAS DE CATEGORÍA POR VEREDICTO
# ---------------------------------------------------------------------------
# El score cae en un nivel (alto/medio/bajo) y aquí se traduce a la etiqueta de
# negocio del contenedor. La mecánica es idéntica; solo cambia el nombre.
CATEGORIAS = {
    "nivel_amenaza":          {"alto": "amenaza_alta",     "medio": "amenaza_media",     "bajo": "amenaza_baja"},
    "lead_score":             {"alto": "lead_caliente",    "medio": "lead_tibio",        "bajo": "lead_frio"},
    "oportunidad_captura":    {"alto": "captura_alta",     "medio": "captura_media",     "bajo": "captura_baja"},
    "relevancia_tecnologica": {"alto": "relevancia_alta",  "medio": "relevancia_media",  "bajo": "relevancia_baja"},
    "alerta_cambio":          {"alto": "alerta_alta",      "medio": "alerta_media",      "bajo": "alerta_baja"},
    "oportunidad_mercado":    {"alto": "oportunidad_alta", "medio": "oportunidad_media", "bajo": "oportunidad_baja"},
    "alerta_regulatoria":     {"alto": "alerta_alta",      "medio": "alerta_media",      "bajo": "alerta_baja"},
    "gap_capacidad":          {"alto": "gap_critico",      "medio": "gap_moderado",      "bajo": "gap_leve"},
}


# ---------------------------------------------------------------------------
# UTILIDADES NUMÉRICAS Y DE TIEMPO
# ---------------------------------------------------------------------------
def _num(valor, defecto: float = 0.0) -> float:
    """QUÉ HACE: convierte a float de forma tolerante.
    PARA QUÉ SIRVE: relevancia, valor_contrato o tracción pueden venir como texto."""
    if isinstance(valor, bool):
        return 1.0 if valor else 0.0
    try:
        return float(valor)
    except (TypeError, ValueError):
        return defecto


def _dias_desde(fecha: datetime | None) -> float | None:
    """QUÉ HACE: días transcurridos desde una fecha hasta ahora.
    PARA QUÉ SIRVE: base del factor de reciencia."""
    if fecha is None:
        return None
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fecha).total_seconds() / 86400.0


def _dias_hasta(fecha: datetime | None) -> float | None:
    """QUÉ HACE: días que faltan hasta una fecha futura.
    PARA QUÉ SIRVE: base de la urgencia de las alertas de vencimiento."""
    d = _dias_desde(fecha)
    return None if d is None else -d


def _texto_senal(s: dict) -> str:
    """QUÉ HACE: junta temas + contenido de una señal en texto minúsculas.
    PARA QUÉ SIRVE: buscar coincidencia con el stack de TAK sin depender de un
    solo campo."""
    temas = s.get("temas") or []
    if isinstance(temas, str):
        temas = [temas]
    partes = [str(t) for t in temas] + [str(s.get("contenido", ""))]
    return " ".join(partes).lower()


def _toca_stack(s: dict) -> bool:
    """QUÉ HACE: dice si una señal menciona una tecnología del stack de TAK.
    PARA QUÉ SIRVE: base del factor 'coincidencia_stack' y de la ventaja de cierre."""
    texto = _texto_senal(s)
    return any(tech in texto for tech in cfg.STACK_TAK)


# ---------------------------------------------------------------------------
# CONTEXTO DE NORMALIZACIÓN (para llevar cada factor a escala 0–1)
# ---------------------------------------------------------------------------
def _contexto(senales_por_entidad: dict[str, list[dict]], ventana_dias: int = 180) -> dict:
    """
    QUÉ HACE
        Calcula los máximos del lote (volumen, necesidades, tracción, valor de
        contrato…) para normalizar cada factor a 0–1 de forma relativa.

    PARA QUÉ SIRVE
        Que el score sea comparable dentro del contenedor: la entidad más activa
        marca el 1.0 en volumen y las demás se miden respecto a ella. Es
        interpretable y defendible ("relativo al competidor más activo del lote").
    """
    def _max(fn):
        vals = [fn(ss) for ss in senales_por_entidad.values()]
        m = max(vals) if vals else 0
        return m if m > 0 else 1  # evita división por cero

    def _cuenta_tipo(ss, *tipos):
        return sum(1 for s in ss if s.get("tipo_senal") in tipos)

    def _max_atrib(ss, *claves):
        mejor = 0.0
        for s in ss:
            atr = s.get("atributos", {}) or {}
            for k in claves:
                mejor = max(mejor, _num(atr.get(k, 0)))
        return mejor

    tipos_presentes = {s.get("tipo_senal") for ss in senales_por_entidad.values() for s in ss}

    return {
        "ventana_dias": ventana_dias,
        "n_tipos_posibles": max(len(tipos_presentes), 1),
        "max_volumen": _max(len),
        "max_necesidades": _max(lambda ss: _cuenta_tipo(ss, "necesidad_tecnologica")),
        "max_insatisfaccion": _max(lambda ss: _cuenta_tipo(ss, "senal_insatisfaccion", "insatisfaccion")),
        "max_traccion": _max(lambda ss: _max_atrib(ss, "traccion", "stars")),
        "max_valor_contrato": _max(lambda ss: _max_atrib(ss, "valor_contrato")),
        "max_demanda": _max(lambda ss: _cuenta_tipo(ss, "demanda_perfil")),
    }


# ---------------------------------------------------------------------------
# CALCULADORES DE FACTOR  (cada uno devuelve un valor en [0, 1])
# ---------------------------------------------------------------------------
def _f_volumen(ss, ctx):
    """Cuánto se mueve la entidad, relativo a la más activa del lote."""
    return min(1.0, len(ss) / ctx["max_volumen"])

def _f_diversidad(ss, ctx):
    """En cuántos frentes distintos se mueve (variedad de tipo_senal)."""
    distintos = len({s.get("tipo_senal") for s in ss})
    return min(1.0, distintos / ctx["n_tipos_posibles"])

def _f_reciencia(ss, ctx):
    """Qué tan fresca es la actividad más reciente (decae linealmente en la ventana)."""
    dias = [_dias_desde(_parse_fecha(s.get("fecha_evento"))) for s in ss]
    dias = [d for d in dias if d is not None]
    if not dias:
        return 0.0
    return max(0.0, 1.0 - min(dias) / ctx["ventana_dias"])

def _f_relevancia(ss, ctx):
    """Relevancia promedio normalizada a 0–1. Lee relevancia.score (formato real
    de Silver, 0–1); tolera también un número plano por compatibilidad."""
    vals = []
    for s in ss:
        rel = s.get("relevancia")
        if isinstance(rel, dict):
            vals.append(_num(rel.get("score")))
        else:
            vals.append(_num(rel))
    prom = sum(vals) / len(vals) if vals else 0.0
    # relevancia.score de Silver ya está en 0–1; si viniera en escala 1–5, se normaliza
    return min(1.0, prom if prom <= 1.0 else prom / 5.0)

def _f_n_necesidades(ss, ctx):
    """Cuántas necesidades tecnológicas manifiesta, relativo al máximo del lote."""
    n = sum(1 for s in ss if s.get("tipo_senal") == "necesidad_tecnologica")
    return min(1.0, n / ctx["max_necesidades"])

def _f_coincidencia_stack(ss, ctx):
    """Fracción de señales que tocan el stack de TAK (ventaja de cierre)."""
    if not ss:
        return 0.0
    return sum(1 for s in ss if _toca_stack(s)) / len(ss)

def _f_insatisfaccion(ss, ctx):
    """Presencia de señales de insatisfacción / posible cambio de proveedor."""
    n = sum(1 for s in ss if s.get("tipo_senal") in ("senal_insatisfaccion", "insatisfaccion"))
    return min(1.0, n / ctx["max_insatisfaccion"])

def _f_traccion(ss, ctx):
    """Tracción de una tendencia (stars/menciones), relativo al máximo del lote."""
    t = 0.0
    for s in ss:
        atr = s.get("atributos", {}) or {}
        t = max(t, _num(atr.get("traccion", atr.get("stars", 0))))
    return min(1.0, t / ctx["max_traccion"])

def _f_valor_contrato(ss, ctx):
    """Valor del contrato/oportunidad (de SECOP), relativo al máximo del lote."""
    v = 0.0
    for s in ss:
        atr = s.get("atributos", {}) or {}
        v = max(v, _num(atr.get("valor_contrato", 0)))
    return min(1.0, v / ctx["max_valor_contrato"])

def _f_demanda_recurrente(ss, ctx):
    """Cuán repetida es la demanda de un perfil/tecnología en el mercado."""
    n = sum(1 for s in ss if s.get("tipo_senal") == "demanda_perfil")
    return min(1.0, n / ctx["max_demanda"])

def _f_brecha_stack(ss, ctx):
    """Brecha: fracción de señales cuya tecnología NO está en el stack de TAK."""
    if not ss:
        return 0.0
    return sum(1 for s in ss if not _toca_stack(s)) / len(ss)


FACTORES = {
    "volumen_señales": _f_volumen,
    "diversidad_tipos": _f_diversidad,
    "reciencia": _f_reciencia,
    "relevancia_promedio": _f_relevancia,
    "n_necesidades": _f_n_necesidades,
    "coincidencia_stack": _f_coincidencia_stack,
    "señal_insatisfaccion": _f_insatisfaccion,
    "traccion": _f_traccion,
    "valor_contrato": _f_valor_contrato,
    "demanda_recurrente": _f_demanda_recurrente,
    "brecha_vs_stack": _f_brecha_stack,
}


# ---------------------------------------------------------------------------
# SCORING DE UNA ENTIDAD
# ---------------------------------------------------------------------------
def _nivel(score: float, umbrales: dict) -> str:
    """QUÉ HACE: mapea un score 0–1 a alto/medio/bajo según los umbrales.
    PARA QUÉ SIRVE: traducir el número a una categoría accionable."""
    if score >= umbrales.get("alto", 0.66):
        return "alto"
    if score >= umbrales.get("medio", 0.33):
        return "medio"
    return "bajo"


def _bonus_urgencia(senales: list[dict], scoring_cfg: dict) -> tuple[float, dict]:
    """
    QUÉ HACE
        Si la entidad tiene una señal de vencimiento próxima (fecha_cierre cercana),
        calcula un bonus de urgencia proporcional a lo cerca que está de cerrar.

    PARA QUÉ SIRVE
        Que una oportunidad de captura o de mercado suba de prioridad cuando el
        reloj corre. Sin esto, una licitación que cierra en 3 días pesaría igual
        que una que cierra en 3 meses.
    """
    bonus_max = scoring_cfg.get("bonus_vencimiento_proximo", 0.0)
    if bonus_max <= 0:
        return 0.0, {}
    dias_min = None
    for s in senales:
        d = _dias_hasta(_parse_fecha(s.get("fecha_cierre")))
        if d is not None and d >= 0:
            dias_min = d if dias_min is None else min(dias_min, d)
    if dias_min is None:
        return 0.0, {}
    # Urgencia: 1.0 si cierra hoy, decae a 0 a los 60 días.
    urgencia = max(0.0, 1.0 - dias_min / 60.0)
    return round(bonus_max * urgencia, 4), {"dias_para_cierre": round(dias_min, 1),
                                            "urgencia": round(urgencia, 3)}


def puntuar_entidad(senales: list[dict], contenedor: str, ctx: dict) -> dict:
    """
    QUÉ HACE
        Calcula el score 0–1 de una entidad aplicando los factores ponderados de
        la config, más el bonus de urgencia, y lo mapea a su categoría.

    PARA QUÉ SIRVE
        Es el veredicto accionable de esa entidad, con desglose completo del
        porqué (cada factor: valor, peso, aporte).

    DEVUELVE
        dict listo para gold_data.guardar_veredictos, con detalle auditable.
    """
    c = cfg.config(contenedor)
    scoring_cfg = c["scoring"]
    factores_cfg = scoring_cfg["factores"]

    aportes = {}
    score_base = 0.0
    for factor, peso in factores_cfg.items():
        calc = FACTORES.get(factor)
        if calc is None:
            log.warning("Factor '%s' sin calculador; se omite.", factor)
            continue
        valor = calc(senales, ctx)
        aporte = round(valor * peso, 4)
        score_base += aporte
        aportes[factor] = {"valor": round(valor, 3), "peso": peso, "aporte": aporte}

    bonus, det_bonus = _bonus_urgencia(senales, scoring_cfg)
    score = min(1.0, round(score_base + bonus, 4))

    nivel = _nivel(score, scoring_cfg["umbrales"])
    veredicto = c["veredicto"]
    categoria = CATEGORIAS.get(veredicto, {}).get(nivel, nivel)

    # Señales de respaldo: las más relevantes primero, hasta 10, para auditar.
    respaldo = sorted(senales, key=lambda s: _num(s.get("relevancia")), reverse=True)
    ids_respaldo = [s.get("id_normalizado", "") for s in respaldo[:10] if s.get("id_normalizado")]

    entidad_nombre = senales[0].get("entidad", "sin_entidad") if senales else "sin_entidad"
    return {
        "entidad_id": entidad_nombre,
        "entidad_nombre": entidad_nombre,
        "tipo_veredicto": veredicto,
        "score": score,
        "categoria": categoria,
        "senales_respaldo": ids_respaldo,
        "detalle": {
            "factores": aportes,
            "bonus_urgencia": bonus,
            "urgencia": det_bonus,
            "n_senales": len(senales),
            "umbrales": scoring_cfg["umbrales"],
        },
    }


# ---------------------------------------------------------------------------
# ORQUESTACIÓN A NIVEL CONTENEDOR
# ---------------------------------------------------------------------------
def _agrupar_por_entidad(registros: list[dict]) -> dict[str, list[dict]]:
    """QUÉ HACE: agrupa los registros de Silver por entidad.
    PARA QUÉ SIRVE: el veredicto es por entidad; esta es la unidad de scoring."""
    grupos: dict[str, list[dict]] = {}
    for r in registros:
        ent = (r.get("entidad") or "sin_entidad").strip() or "sin_entidad"
        grupos.setdefault(ent, []).append(r)
    return grupos


def calcular_veredictos(registros: list[dict], contenedor: str,
                        ventana_dias: int = 180) -> list[dict]:
    """
    QUÉ HACE
        Produce el veredicto accionable de cada entidad de un contenedor.

    PARA QUÉ SIRVE
        Es la función pública que usa el orquestador. Devuelve la lista lista para
        gold_data.guardar_veredictos, ordenada por score descendente (lo más
        accionable primero).
    """
    grupos = _agrupar_por_entidad(registros)
    ctx = _contexto(grupos, ventana_dias)
    veredictos = [puntuar_entidad(ss, contenedor, ctx) for ss in grupos.values()]
    veredictos.sort(key=lambda v: v["score"], reverse=True)
    log.info("Scoring '%s': %d veredictos sobre %d entidades.",
             contenedor, len(veredictos), len(grupos))
    return veredictos


def generar_alertas(registros: list[dict], contenedor: str) -> list[dict]:
    """
    QUÉ HACE
        Recorre las señales de vencimiento (con fecha de cierre) de un contenedor y
        produce alertas priorizadas por urgencia (días restantes ascendente).

    PARA QUÉ SIRVE
        Es la salida que evita que TAK pierda licitaciones, convocatorias o el fin
        de un contrato por caducidad. Cada alerta es un item accionable con su
        cuenta regresiva. Solo aplica a contenedores con señales de vencimiento.

    DEVUELVE
        Lista de alertas ordenadas por urgencia (lo que cierra antes, primero).
    """
    inds = cfg.indicadores_alimentables(contenedor)
    # tipos de señal de vencimiento + contratos vigentes cuyo fin dispara oportunidad
    tipos_venc = {
        ind.get("tipo_senal") for ind in inds.values()
        if (ind.get("naturaleza") == "vencimiento" or ind.get("requiere_fecha_cierre"))
        and ind.get("tipo_senal")
    }
    if not tipos_venc:
        return []

    alertas = []
    for r in registros:
        if r.get("tipo_senal") not in tipos_venc:
            continue
        dias = _dias_hasta(_parse_fecha(r.get("fecha_cierre")))
        if dias is None or dias < 0:
            continue  # sin fecha o ya vencida → no es alerta accionable
        alertas.append({
            "entidad_id": r.get("entidad", "sin_entidad"),
            "entidad_nombre": r.get("entidad", "sin_entidad"),
            "tipo_alerta": r.get("tipo_senal"),
            "dias_restantes": round(dias, 1),
            "fecha_cierre": r.get("fecha_cierre"),
            "coincide_stack": _toca_stack(r),
            "id_normalizado": r.get("id_normalizado", ""),
            "detalle": {"atributos": r.get("atributos", {})},
        })
    alertas.sort(key=lambda a: a["dias_restantes"])  # más urgente primero
    log.info("Alertas '%s': %d oportunidades con vencimiento.", contenedor, len(alertas))
    return alertas


# ===========================================================================
# PRUEBA AUTÓNOMA (corre sin Azure)
#   Ejecuta:  python scoring.py
# ===========================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from datetime import timedelta
    hoy = datetime.now(timezone.utc)

    # --- Demo 1: veredicto de amenaza (competidores) ---
    print("\n=== PRUEBA 1: veredicto de amenaza (competidores) ===\n")
    registros_comp = [
        {"id_normalizado": "1", "entidad": "CompetidorA", "tipo_senal": "lanzamiento",
         "relevancia": 5, "fecha_evento": (hoy - timedelta(days=10)).isoformat(), "temas": ["oracle cloud"]},
        {"id_normalizado": "2", "entidad": "CompetidorA", "tipo_senal": "alianza",
         "relevancia": 4, "fecha_evento": (hoy - timedelta(days=20)).isoformat(), "temas": ["aws"]},
        {"id_normalizado": "3", "entidad": "CompetidorA", "tipo_senal": "expansion",
         "relevancia": 4, "fecha_evento": (hoy - timedelta(days=5)).isoformat(), "temas": ["bogota"]},
        {"id_normalizado": "4", "entidad": "CompetidorB", "tipo_senal": "lanzamiento",
         "relevancia": 2, "fecha_evento": (hoy - timedelta(days=150)).isoformat(), "temas": ["marketing"]},
    ]
    for v in calcular_veredictos(registros_comp, "competidores"):
        print(f"  {v['entidad_nombre']:14} score={v['score']:.3f}  → {v['categoria']}")
        for f, d in v["detalle"]["factores"].items():
            print(f"       {f:22} valor={d['valor']:.2f} × peso={d['peso']:.2f} = {d['aporte']:.3f}")
    print("  → CompetidorA debe salir con amenaza mayor: más señales, más diversas, más recientes.")

    # --- Demo 2: alertas de vencimiento (mercado) ---
    print("\n=== PRUEBA 2: alertas de vencimiento (mercado) ===\n")
    registros_merc = [
        {"id_normalizado": "10", "entidad": "AlcaldiaX", "tipo_senal": "licitacion",
         "fecha_cierre": (hoy + timedelta(days=3)).isoformat(), "temas": ["oracle"],
         "atributos": {"valor_contrato": 500000000}},
        {"id_normalizado": "11", "entidad": "MinisterioY", "tipo_senal": "licitacion",
         "fecha_cierre": (hoy + timedelta(days=45)).isoformat(), "temas": ["redes"],
         "atributos": {"valor_contrato": 120000000}},
        {"id_normalizado": "12", "entidad": "EntidadZ", "tipo_senal": "licitacion",
         "fecha_cierre": (hoy - timedelta(days=2)).isoformat(), "temas": ["cloud"],
         "atributos": {}},  # ya vencida → NO debe aparecer
    ]
    for a in generar_alertas(registros_merc, "mercado"):
        stack = "coincide stack TAK" if a["coincide_stack"] else "—"
        print(f"  {a['entidad_nombre']:14} cierra en {a['dias_restantes']:>5} días  [{stack}]")
    print("  → AlcaldiaX (3 días) primero; la ya vencida no aparece.\n")