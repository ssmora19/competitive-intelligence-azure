# -*- coding: utf-8 -*-
"""
enriquecimiento.py
==================

QUÉ HACE
--------
Agrega la SEÑAL que Gold necesita para analizar: clasifica el tipo de señal de
cada ítem, puntúa su relevancia y (opcionalmente) estima sentimiento. Implementa
la estrategia híbrida reglas -> Hugging Face -> LLM, dosificada por dificultad.

PARA QUÉ SIRVE
--------------
En el Ciclo de Inteligencia Competitiva (CIC), esta es la fase de PROCESSING /
ANALYSIS hecha infraestructura: estructurar y categorizar el dato. Silver no
concluye (eso es Gold), pero sí etiqueta para que Gold pueda agregar.

SUSTENTO DE LA ESTRATEGIA (defensa)
-----------------------------------
- Reglas para lo determinista/semántico simple: barato y 100% reproducible.
- Hugging Face (gratis) para el volumen que una regla no captura.
- LLM SOLO para casos difíciles (alta ambigüedad), dosificado por DIFICULTAD del
  ítem, NO por canal. Dosificar por canal degradaría la calidad de los canales
  no-LinkedIn y rompería el argumento de alcance multicanal uniforme.
- HF y LLM están APAGADOS por defecto (config): el pipeline corre completo solo
  con reglas, sin dependencias pesadas ni gasto de presupuesto. Se activan por
  configuración cuando se disponga de ellos.
"""

from __future__ import annotations

from . import config_silver as cfg
from . import calidad


# ---------------------------------------------------------------------------
# CLASIFICACIÓN
# ---------------------------------------------------------------------------

# Tipo de señal base por familia (lo determinista: no requiere leer el texto).
_TIPO_BASE_POR_FAMILIA = {
    "secop_procesos": "contratacion_publica",
    "secop_contratos": "contratacion_publica",
    "linkedin_jobs": "demanda_talento",
    "linkedin_post": "contenido_corporativo",
    "facebook": "contenido_corporativo",
    "instagram": "contenido_corporativo",
    "tiktok": "contenido_corporativo",
    "x": "contenido_corporativo",
    "youtube": "contenido_audiovisual",
    "github": "actividad_tecnica",
    "web_firecrawl": "referencia_web",
    "noticia_rss": "noticia",  # se refina abajo por palabras clave
}

# Léxico para refinar noticias/posts por tema (dimensión: enriquecimiento semántico).
_LEXICO_TEMA = {
    "lanzamiento": ["lanza", "lanzamiento", "nuevo producto", "presenta", "estrena", "unveil", "launch"],
    "inversion": ["inversión", "inversion", "ronda", "capital", "financiación", "funding", "adquiere", "compra"],
    "alianza": ["alianza", "acuerdo", "partnership", "convenio", "colabora", "firma con"],
    "regulatorio": ["decreto", "ley", "resolución", "resolucion", "norma", "regula", "sanción", "sancion"],
    "expansion": ["expande", "expansión", "expansion", "abre", "nueva sede", "data center", "entra al mercado"],
    "contratacion_publica": ["licitación", "licitacion", "contrato", "adjudica", "secop"],
}


def _clasificar_por_reglas(canonico: dict):
    """
    QUÉ HACE: asigna tipo de señal con reglas (familia + léxico), devolviendo también una confianza.
    PARA QUÉ SIRVE: primer nivel de la estrategia híbrida; resuelve la mayoría de casos sin costo.
    Devuelve (tipo, confianza[0..1]).
    """
    familia = canonico.get("familia")
    base = _TIPO_BASE_POR_FAMILIA.get(familia, "otro")

    # Familias deterministas: alta confianza sin leer texto.
    if familia in ("secop_procesos", "secop_contratos", "linkedin_jobs", "github", "youtube", "web_firecrawl"):
        return base, 0.95

    # Noticias/posts: se intenta refinar por léxico.
    texto = f"{canonico.get('titulo','')} {canonico.get('descripcion','')}".lower()
    texto_norm = calidad._normalizar_nombre(texto)
    mejor_tema, mejor_hits = None, 0
    for tema, claves in _LEXICO_TEMA.items():
        hits = sum(1 for c in claves if calidad._normalizar_nombre(c) in texto_norm)
        if hits > mejor_hits:
            mejor_tema, mejor_hits = tema, hits

    if mejor_tema and mejor_hits >= 1:
        # Confianza crece con la cantidad de coincidencias, con techo.
        return mejor_tema, min(0.60 + 0.15 * mejor_hits, 0.9)
    # Sin señal de léxico: queda el tipo base con confianza baja (candidato a HF/LLM).
    return base, 0.5


def _clasificar_hf(canonico: dict):
    """
    QUÉ HACE: (opcional) clasifica con un modelo de Hugging Face zero-shot.
    PARA QUÉ SIRVE: segundo nivel de la estrategia; captura matiz que las reglas
    no ven. Apagado por defecto (cfg.USAR_HUGGINGFACE). Degrada a None si la
    librería no está instalada, para no romper el pipeline.
    """
    if not cfg.USAR_HUGGINGFACE:
        return None
    try:
        from transformers import pipeline  # import perezoso: solo si se activa
    except Exception:
        return None
    # Punto de extensión: aquí se instanciaría un pipeline 'zero-shot-classification'
    # cacheado a nivel de módulo. Se deja como hook para no acoplar Silver a un
    # modelo concreto ni forzar la dependencia en la ejecución base.
    return None


def _clasificar_llm(canonico: dict):
    """
    QUÉ HACE: (opcional) clasifica con LLM SOLO ítems difíciles.
    PARA QUÉ SIRVE: tercer nivel, dosificado por dificultad. Apagado por defecto
    (cfg.USAR_LLM). Hook para conectar el proveedor cuando haya presupuesto.
    """
    if not cfg.USAR_LLM:
        return None
    return None


def clasificar(canonico: dict) -> dict:
    """
    QUÉ HACE: orquesta la clasificación híbrida y devuelve el veredicto con el
    método usado y la confianza.
    PARA QUÉ SIRVE: dejar registrado no solo QUÉ se clasificó sino CÓMO (reglas /
    hf / llm) y con qué confianza — trazabilidad para la validación cuantitativa.
    """
    tipo, conf = _clasificar_por_reglas(canonico)
    metodo = "reglas"

    # Dosificación por DIFICULTAD: solo si las reglas quedaron poco seguras se
    # escala a HF y, si sigue difícil, a LLM.
    if conf < cfg.UMBRAL_CONFIANZA_REGLAS:
        hf = _clasificar_hf(canonico)
        if hf:
            tipo, conf, metodo = hf.get("tipo", tipo), hf.get("confianza", conf), "hf"
            if conf < cfg.UMBRAL_CONFIANZA_REGLAS:
                llm = _clasificar_llm(canonico)
                if llm:
                    tipo, conf, metodo = llm.get("tipo", tipo), llm.get("confianza", conf), "llm"

    return {"tipo_senal": tipo, "metodo": metodo, "confianza": round(conf, 2)}


# ---------------------------------------------------------------------------
# RELEVANCIA  (dimensión de calidad: RELEVANCIA)
# ---------------------------------------------------------------------------

# Cache del universo de entidades monitoreadas (se carga una sola vez).
_ENTIDADES_CACHE = None


def _entidades_monitoreadas():
    """QUÉ HACE: carga (y cachea) el universo de entidades. PARA QUÉ SIRVE: no releer los 3 archivos en cada registro."""
    global _ENTIDADES_CACHE
    if _ENTIDADES_CACHE is None:
        try:
            from . import entidades as _ent
            _ENTIDADES_CACHE = _ent.cargar_entidades(".")
        except Exception:
            _ENTIDADES_CACHE = set()
    return _ENTIDADES_CACHE


def _es_sector_ti(texto_norm: str) -> bool:
    """QUÉ HACE: indica si el texto toca el sector TI por léxico. PARA QUÉ SIRVE: componente 2 del filtro (Regla A)."""
    for termino in cfg.TERMINOS_SECTOR_TI:
        if calidad._normalizar_nombre(termino) in texto_norm:
            return True
    return False


def puntuar_relevancia(canonico: dict) -> dict:
    """
    QUÉ HACE: decide la relevancia de forma SENSIBLE AL TIPO DE FUENTE, porque no
    todo el contenido de la Silver es de la misma naturaleza y no admite el mismo
    criterio. Se distinguen tres grupos según la variable de IC (contenedor):

      A) ACTORES (competidores, clientes, clientes_competencia):
         Aquí vive la señal de inteligencia competitiva pura. Se aplica la REGLA B
         validada en el experimento (81%): relevante si menciona ENTIDAD
         MONITOREADA **Y** hay EVENTO DE NEGOCIO/TI. Filtra el ruido (SETI,
         cocaína, farándula) que se colaba antes.

      B) SECTOR (tendencias, mercado, proveedores, regulatorio):
         Son señales del entorno, no de un actor concreto. Es relevante si el
         contenido es del SECTOR TI (aunque no mencione a un competidor puntual),
         o si menciona una entidad. Criterio más amplio, acorde a su naturaleza.

      C) PROPIAS (capacidades):
         Es la presencia de TAK monitoreándose a sí mismo. Relevante por
         construcción: no se filtra la propia empresa.

    PARA QUÉ SIRVE: evita el error de aplicar un criterio único a contenidos
    distintos. La Regla B, pensada para noticias de competidores, dejaría en cero
    las capacidades de TAK y castigaría las tendencias del sector; segmentar por
    tipo de fuente corrige eso sin perder el filtrado de ruido donde importa.

    ALCANCE DE VALIDACIÓN (honesto para defensa): la Regla B (grupo A) se validó
    cuantitativamente sobre noticias/publicaciones de actores (81%). Los criterios
    de los grupos B y C se derivan de la naturaleza de cada fuente; su validación
    cuantitativa queda propuesta como trabajo futuro.

    NOTA: SECOP y redes de un actor concreto son relevantes por construcción; solo
    se penaliza la discrepancia de entidad.
    """
    familia = canonico.get("familia")
    variable = (canonico.get("variable_ic") or "").lower()
    umbral = cfg.umbral_relevancia_de(familia)
    flags = set(canonico.get("calidad", {}).get("flags", []))
    tema = (canonico.get("clasificacion", {}) or {}).get("tipo_senal", "")

    texto_norm = calidad._normalizar_nombre(
        f"{canonico.get('titulo','')} {canonico.get('descripcion','')} "
        f"{canonico.get('entidad_monitoreada','')} {canonico.get('termino_busqueda','')}"
    )

    TEMAS_NEGOCIO = {
        "contratacion_publica", "alianza", "lanzamiento",
        "inversion", "expansion", "regulatorio", "demanda_talento",
    }
    FAMILIAS_DESCUBRIMIENTO = ("noticia_rss", "web_firecrawl", "linkedin_jobs", "github")

    # Clasificación del contenedor en los tres grupos.
    GRUPO_ACTORES = ("competidores", "clientes", "clientes_competencia", "clientes-competencia")
    GRUPO_SECTOR = ("tendencias", "mercado", "proveedores", "regulatorio")
    GRUPO_PROPIAS = ("capacidades",)

    def _menciona_entidad():
        ents = _entidades_monitoreadas()
        if not ents:
            return None
        from . import entidades as _ent
        return _ent.menciona_entidad(texto_norm, ents)

    motivo = None

    # --- GRUPO C: capacidades (presencia de TAK) -> relevante por construcción ---
    if variable in GRUPO_PROPIAS:
        score, motivo = 1.0, "capacidades_propias"

    # --- Familias de ENTIDAD ya ligada (SECOP, redes de actor) -> relevantes ---
    elif familia not in FAMILIAS_DESCUBRIMIENTO:
        score, motivo = 1.0, "familia_entidad"

    # --- GRUPO A: actores -> REGLA B validada (entidad + evento) ---
    elif variable in GRUPO_ACTORES:
        ent = _menciona_entidad()
        es_evento = tema in TEMAS_NEGOCIO
        if ent and es_evento:
            score, motivo = 1.0, f"entidad+evento:{ent}/{tema}"
        elif es_evento and _es_sector_ti(texto_norm):
            score, motivo = 0.8, f"evento_ti:{tema}"
        elif ent and not es_evento:
            score, motivo = 0.3, f"solo_mencion:{ent}/{tema}"
        else:
            score, motivo = 0.0, "ni_entidad_ni_evento"

    # --- GRUPO B: sector -> relevante si es sector TI o menciona entidad ---
    elif variable in GRUPO_SECTOR:
        ent = _menciona_entidad()
        if ent:
            score, motivo = 1.0, f"entidad_sector:{ent}"
        elif _es_sector_ti(texto_norm):
            score, motivo = 0.8, "sector_ti"
        else:
            score, motivo = 0.0, "fuera_de_sector"

    # --- Cualquier otro caso no previsto: criterio conservador (dejar pasar) ---
    else:
        score, motivo = 0.8, "sin_grupo_definido"

    if "entidad_discrepante" in flags:
        score = min(score, 0.4)

    return {
        "score": round(score, 2),
        "pasa_umbral": score >= umbral,
        "umbral": umbral,
        "motivo": motivo,
    }


# ---------------------------------------------------------------------------
# SENTIMIENTO  (opcional)
# ---------------------------------------------------------------------------

_POS = ["crece", "líder", "lider", "premio", "gana", "éxito", "exito", "innov", "mejora", "impulsa"]
_NEG = ["cae", "pérdida", "perdida", "crisis", "sanción", "sancion", "demanda", "falla", "cierre", "despido"]


def estimar_sentimiento(canonico: dict) -> dict | None:
    """
    QUÉ HACE: estimación LIGERA de sentimiento por léxico (positivo/neutro/negativo).
    PARA QUÉ SIRVE: aporta a Gold una lectura de tono. Es un placeholder por reglas;
    el camino de mayor precisión (Hugging Face) queda como hook y apagado por
    defecto, coherente con la estrategia híbrida y el presupuesto. Devuelve None
    para familias donde el sentimiento no aplica (SECOP, GitHub, web-índice).
    """
    familia = canonico.get("familia")
    if familia in ("secop_procesos", "secop_contratos", "github", "web_firecrawl", "linkedin_jobs"):
        return None
    texto = calidad._normalizar_nombre(f"{canonico.get('titulo','')} {canonico.get('descripcion','')}")
    pos = sum(1 for w in _POS if calidad._normalizar_nombre(w) in texto)
    neg = sum(1 for w in _NEG if calidad._normalizar_nombre(w) in texto)
    if pos == neg:
        etiqueta, score = "neutro", 0.0
    elif pos > neg:
        etiqueta, score = "positivo", round(min((pos - neg) / 3, 1.0), 2)
    else:
        etiqueta, score = "negativo", round(-min((neg - pos) / 3, 1.0), 2)
    return {"etiqueta": etiqueta, "score": score, "metodo": "reglas"}
