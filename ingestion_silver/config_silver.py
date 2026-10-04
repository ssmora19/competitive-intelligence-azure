# -*- coding: utf-8 -*-
"""
config_silver.py
================

QUÉ HACE
--------
Centraliza TODOS los parámetros de negocio y de operación de la capa Silver:
qué familias existen, cuánto pesa cada una (prioridad operativa), qué ventana
de recencia aplica, qué umbral de relevancia se exige y a qué contenedor Silver
se escribe cada variable de IC.

PARA QUÉ SIRVE
--------------
Para que ninguna decisión quede "hardcodeada" dentro de la lógica. En la
sustentación, cuando pregunten "¿por qué LinkedIn pesa más?" o "¿por qué una
noticia de 2015 se marca como no vigente?", la respuesta es: es un PARÁMETRO
explícito y configurable, no una regla escondida. Cambiar una prioridad o una
ventana es editar una línea aquí, sin tocar el pipeline.

SUSTENTO
--------
- La prioridad operativa modela el énfasis en LinkedIn que pidió TAK como un
  PESO configurable (no como un estándar de calidad diferenciado), preservando
  que las 12 familias se procesen al MISMO estándar. Así el alcance multicanal
  del artefacto sigue siendo defendible.
- La recencia y la relevancia son dimensiones de calidad de datos (DAMA-DMBOK /
  ISO 25012): "vigencia" y "relevancia". Aquí viven sus criterios de aceptación.
"""

# Versión del esquema canónico que produce Silver (para trazabilidad futura).
SCHEMA_SILVER_VERSION = "1.0"

# ---------------------------------------------------------------------------
# FAMILIAS RECONOCIDAS
# ---------------------------------------------------------------------------
# Las 12 familias estructurales detectadas al inspeccionar Bronze. El nombre de
# 'fuente' NO se usa como llave (demostró ser inconsistente: 'patentes_tech' es
# RSS pero 'inversion_tech' es web); la familia se resuelve por ESTRUCTURA del
# 'raw' en dispatcher.py. Esta lista es el universo cerrado de familias válidas.
FAMILIAS = (
    "secop_procesos",
    "secop_contratos",   # incluye la variante 'adjudicatario' (mismo raw, distinto rol)
    "noticia_rss",       # 2 sub-formas: google_news vs rss directo
    "web_firecrawl",     # páginas-índice de scraping
    "linkedin_post",
    "linkedin_jobs",
    "youtube",
    "github",
    "facebook",
    "instagram",
    "tiktok",
    "x",
)

# ---------------------------------------------------------------------------
# PRIORIDAD OPERATIVA  (énfasis del cliente, NO estándar de calidad)
# ---------------------------------------------------------------------------
# TAK priorizó LinkedIn. Se modela como un peso en la salida: Gold puede ordenar,
# filtrar o segmentar el dashboard por este campo. Todo lo demás es "base".
# Reajustar el énfasis del cliente = editar este diccionario.
PRIORIDAD_OPERATIVA = {
    "linkedin_post": "alta",
    "linkedin_jobs": "alta",
    # el resto hereda "base" vía PRIORIDAD_DEFAULT
}
PRIORIDAD_DEFAULT = "base"

# ---------------------------------------------------------------------------
# VENTANA DE RECENCIA  (dimensión de calidad: VIGENCIA)
# ---------------------------------------------------------------------------
# Días hacia atrás dentro de los cuales un ítem se considera "vigente".
# Distinta por familia porque el ritmo de cada canal es distinto: un contrato
# público mantiene valor más tiempo que un post de red social.
# Un ítem fuera de ventana NO se descarta: se conserva con vigente=False y Gold
# decide si lo usa. (Evidencia real: noticia ANSPE de 2015, tweet de 2022.)
VENTANA_RECENCIA_DIAS = {
    "secop_procesos": 730,
    "secop_contratos": 730,
    "noticia_rss": 365,
    "web_firecrawl": 365,
    "linkedin_post": 180,
    "linkedin_jobs": 120,
    "youtube": 365,
    "github": 540,
    "facebook": 180,
    "instagram": 180,
    "tiktok": 180,
    "x": 180,
}
VENTANA_RECENCIA_DEFAULT = 365

# ---------------------------------------------------------------------------
# UMBRAL DE RELEVANCIA  (dimensión de calidad: RELEVANCIA)
# ---------------------------------------------------------------------------
# Score mínimo [0..1] para que un ítem se marque como relevante.
# Alto en las familias de DESCUBRIMIENTO (RSS temático, GitHub por topic,
# LinkedIn Jobs por keyword) porque son ruidosas por naturaleza — ahí es donde
# aparecieron los falsos positivos (Exadata->petrolera, 'itis'->colegio italiano).
# Cero (siempre pasa) en familias de ENTIDAD conocida (SECOP, redes de un actor
# identificado), donde la relevancia se garantiza por la propia búsqueda.
UMBRAL_RELEVANCIA = {
    # Con el filtro Regla A, el score es 0.0 (ruido) / 0.8 (sector TI) / 1.0 (entidad).
    # Umbral 0.5 => deja pasar TI y entidad, filtra el ruido. Uniforme para las
    # familias de descubrimiento (donde entra el ruido).
    "linkedin_jobs": 0.50,
    "github": 0.50,
    "noticia_rss": 0.50,
    "web_firecrawl": 0.50,
    "linkedin_post": 0.0,
    "facebook": 0.0,
    "instagram": 0.0,
    "tiktok": 0.0,
    "x": 0.0,
    "youtube": 0.0,
    "secop_procesos": 0.0,
    "secop_contratos": 0.0,
}
UMBRAL_RELEVANCIA_DEFAULT = 0.0

# ---------------------------------------------------------------------------
# UMBRALES DE PRECISIÓN  (validación cuantitativa, diferenciados por tarea)
# ---------------------------------------------------------------------------
# Criterio de aceptación de cada tarea de enriquecimiento, medido contra un
# conjunto de validación etiquetado a mano. Se usa precisión como métrica
# principal porque en IC el costo de un falso positivo pesa más que el de un
# falso negativo (alertar algo que no es señal es peor que dejar pasar una).
#
# Son DOS umbrales distintos, y la diferencia está justificada por la naturaleza
# de cada tarea:
#   - Clasificación de temas (85%): tarea más estructurada y objetiva. Un texto
#     sobre una alianza es, objetivamente, sobre una alianza -> se le exige más.
#   - Análisis de sentimiento (80%): tarea subjetiva, donde incluso las
#     percepciones humanas varían -> exigir el mismo rigor sería irreal.
#
# Estos umbrales son el CRITERIO contra el que se compara tanto el enfoque por
# reglas como (opcionalmente) el de modelos Hugging Face. El enfoque que se use
# en producción es el que, habiendo sido medido, alcance su umbral.
UMBRAL_PRECISION_CLASIFICACION = 0.85  # temas: tarea estructurada
UMBRAL_PRECISION_SENTIMIENTO = 0.80    # sentimiento: tarea subjetiva

# ---------------------------------------------------------------------------
# ESTRATEGIA DE ENRIQUECIMIENTO  (híbrida, dosificada por dificultad)
# ---------------------------------------------------------------------------
# Reglas -> Hugging Face -> LLM. Los dos últimos son OPCIONALES y están apagados
# por defecto para que el pipeline corra sin dependencias pesadas ni gasto de
# presupuesto. Se activan por configuración cuando se quieran usar.
# El LLM se dosifica por DIFICULTAD DEL ÍTEM (ambigüedad), nunca por canal:
# dosificar por canal degradaría la calidad de los canales no-LinkedIn.
USAR_HUGGINGFACE = False
USAR_LLM = False
# Un ítem se considera "difícil" (candidato a HF/LLM) si la confianza de las
# reglas queda por debajo de este valor.
UMBRAL_CONFIANZA_REGLAS = 0.60

# ---------------------------------------------------------------------------
# RUTEO A CONTENEDORES SILVER
# ---------------------------------------------------------------------------
# Cada blob de Bronze trae su 'variable_ic' en _meta; se escribe al contenedor
# Silver correspondiente. Se puede sobreescribir un mapeo puntual aquí.
SILVER_CONTAINER_OVERRIDES = {
    # "clientes_competencia": "silver-clientes-competencia",
}


def contenedor_silver_para(variable_ic: str) -> str:
    """
    QUÉ HACE: devuelve el nombre del contenedor Silver destino para una variable de IC.
    PARA QUÉ SIRVE: rutea cada registro normalizado al contenedor correcto sin
    depender del contenedor Bronze de origen, solo de la variable de IC del dato.
    """
    if variable_ic in SILVER_CONTAINER_OVERRIDES:
        return SILVER_CONTAINER_OVERRIDES[variable_ic]
    base = (variable_ic or "desconocido").replace("_", "-")
    return f"silver-{base}"


def prioridad_de(familia: str) -> str:
    """QUÉ HACE: prioridad operativa de una familia. PARA QUÉ SIRVE: aplicar el énfasis del cliente como peso."""
    return PRIORIDAD_OPERATIVA.get(familia, PRIORIDAD_DEFAULT)


def ventana_recencia_de(familia: str) -> int:
    """QUÉ HACE: ventana de recencia (días) de una familia. PARA QUÉ SIRVE: evaluar la dimensión vigencia."""
    return VENTANA_RECENCIA_DIAS.get(familia, VENTANA_RECENCIA_DEFAULT)


def umbral_relevancia_de(familia: str) -> float:
    """QUÉ HACE: umbral de relevancia de una familia. PARA QUÉ SIRVE: aceptar/marcar por la dimensión relevancia."""
    return UMBRAL_RELEVANCIA.get(familia, UMBRAL_RELEVANCIA_DEFAULT)


# ---------------------------------------------------------------------------
# SECTOR TI  (para el filtro de relevancia, Regla A: entidad O sector TI)
# ---------------------------------------------------------------------------
# Una noticia/post es relevante si menciona una entidad monitoreada O si trata
# del sector TI/tecnologia. Estos terminos definen "sector TI". Se comparan
# normalizados (minusculas, sin acentos). Bilingue ES/EN por el contenido mixto.
TERMINOS_SECTOR_TI = [
    "nube", "cloud", "software", "hardware", "datos", "data", "dato",
    "ciberseguridad", "cybersecurity", "seguridad informatica",
    "inteligencia artificial", "artificial intelligence", " ia ", " ai ",
    "machine learning", "aprendizaje automatico", "data center", "centro de datos",
    "telecomunicaciones", "telecom", "transformacion digital", "digital transformation",
    "servicios digitales", "infraestructura ti", "saas", "paas", "iaas",
    "tecnologia", "technology", "tech", "digital", "gemini", "modelo",
    "chip", "semiconductor", "5g", "fibra optica", "startup", "fintech",
    "aplicacion", "app", "plataforma", "platform", "api", "algoritmo",
    "computacion", "computing", "servidor", "server", "red ", "network",
    "innovacion tecnologica", "robot", "automatizacion", "blockchain",
]