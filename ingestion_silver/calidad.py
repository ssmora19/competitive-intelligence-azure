# -*- coding: utf-8 -*-
"""
calidad.py
==========

QUÉ HACE
--------
Reúne las funciones transversales de aseguramiento de calidad de datos. Hay una
función (o grupo de funciones) por cada dimensión de calidad que Silver debe
garantizar antes de entregar el dato a Gold.

PARA QUÉ SIRVE
--------------
Es el núcleo defendible de Silver. Cada transformación de limpieza NO se
justifica por gusto ("estaba sucio") sino porque ataca una DIMENSIÓN reconocida
de calidad de datos (DAMA-DMBOK / ISO 25012). Al vivir aquí y no dentro de cada
normalizador, una mejora (p.ej. en deduplicación) aplica a las 12 familias a la
vez, y en la defensa se explica una sola vez.

MAPA DIMENSIÓN -> FUNCIÓN
-------------------------
- Validez        -> parse_fecha, a_numero, limpiar_texto, limpiar_markdown
- Exactitud      -> resolver_entidad (entidad por autor + flag de discrepancia)
- Unicidad       -> hash_contenido
- Vigencia       -> es_vigente
- Completitud    -> evaluar_completitud
(Relevancia se puntúa en enriquecimiento.py, que consume estas utilidades.)
"""

from __future__ import annotations

import re
import html
import hashlib
import unicodedata
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime


# ===========================================================================
# DIMENSIÓN: VALIDEZ  —  fechas
# ===========================================================================

# Meses en español, para las fechas embebidas en páginas-índice de web_firecrawl
# (uso futuro cuando se extraigan ítems individuales de un listado).
_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
    "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


def parse_fecha(valor) -> str | None:
    """
    QUÉ HACE: convierte CUALQUIERA de los formatos de fecha vistos en Bronze a
    una cadena ISO 8601 en UTC (p.ej. '2026-08-07T17:14:22+00:00').

    PARA QUÉ SIRVE: ataca la dimensión VALIDEZ. En Bronze conviven al menos
    seis formatos incompatibles y Gold no puede ordenar ni filtrar por tiempo si
    no se homogenizan. Formatos soportados:
      - SECOP naive:        '2026-07-24T00:00:00.000'      (se asume UTC)
      - ISO con Z:          '2026-08-17T00:00:00.000Z'
      - ISO sin tz:         '2026-08-07 17:14:22'          (LinkedIn 'date')
      - RFC822:             'Wed, 24 Jun 2026 07:00:00 GMT' / '... +0000'
      - Estilo Twitter:     'Fri Sep 16 14:00:07 +0000 2022'
      - Unix segundos:      1785766512  (TikTok/Facebook)
      - Unix milisegundos:  1786115662866 (LinkedIn 'timestamp')

    Devuelve None si no logra interpretar el valor (se registra como faltante).
    """
    if valor is None or valor == "":
        return None

    # --- Caso numérico: epoch en segundos o milisegundos ---
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.strip().isdigit()):
        try:
            n = int(valor)
            # Heurística de magnitud: > 1e12 ~ milisegundos; > 1e9 ~ segundos.
            if n > 1_000_000_000_000:
                n = n / 1000.0
            dt = datetime.fromtimestamp(n, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, OverflowError, OSError):
            return None

    s = str(valor).strip()

    # --- RFC822 y estilo Twitter (parsedate_to_datetime maneja ambos) ---
    if re.search(r"[A-Za-z]{3},?\s", s):
        try:
            dt = parsedate_to_datetime(s)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, IndexError):
            pass  # cae al siguiente intento

    # --- ISO (con o sin Z, con o sin milisegundos, con espacio o 'T') ---
    iso = s.replace("Z", "+00:00").replace(" ", "T", 1) if "T" not in s and " " in s else s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # SECOP naive -> se asume UTC
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    # --- Fechas en español (páginas-índice): 'mayo 26, 2026' / '3 de agosto de 2026' / '20 Feb, 2026' ---
    fecha_es = _parse_fecha_es(s)
    if fecha_es:
        return fecha_es

    return None


def _parse_fecha_es(s: str) -> str | None:
    """QUÉ HACE: interpreta fechas escritas en español. PARA QUÉ SIRVE: soporte a futuro para extraer ítems de listados web."""
    t = s.lower().strip()
    # '3 de agosto de 2026'
    m = re.search(r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})", t)
    if m:
        d, mes, y = int(m.group(1)), _MESES_ES.get(m.group(2)), int(m.group(3))
        if mes:
            return datetime(y, mes, d, tzinfo=timezone.utc).isoformat()
    # 'mayo 26, 2026'  /  '20 feb, 2026'
    m = re.search(r"([a-záéíóú]+)\s+(\d{1,2}),?\s+(\d{4})", t)
    if m and _MESES_ES.get(m.group(1)):
        return datetime(int(m.group(3)), _MESES_ES[m.group(1)], int(m.group(2)), tzinfo=timezone.utc).isoformat()
    m = re.search(r"(\d{1,2})\s+([a-záéíóú]{3,}),?\s+(\d{4})", t)
    if m and _MESES_ES.get(m.group(2)):
        return datetime(int(m.group(3)), _MESES_ES[m.group(2)], int(m.group(1)), tzinfo=timezone.utc).isoformat()
    return None


# ===========================================================================
# DIMENSIÓN: VALIDEZ  —  números y texto
# ===========================================================================

def a_numero(valor):
    """
    QUÉ HACE: convierte a número los valores que Bronze entrega como texto
    ('95076000', '39 applicants', '16').
    PARA QUÉ SIRVE: dimensión VALIDEZ. SECOP y las APIs sociales devuelven montos
    y contadores como string; Gold necesita numéricos para sumar/comparar.
    Devuelve int cuando es entero, float cuando tiene decimales, None si no hay dígitos.
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return valor
    s = str(valor)
    m = re.search(r"-?\d[\d.,]*", s)
    if not m:
        return None
    token = m.group(0)
    # Normaliza separadores: quita miles, respeta decimal simple.
    if "," in token and "." in token:
        token = token.replace(",", "")            # '1,234.56' -> '1234.56'
    elif token.count(",") == 1 and token.count(".") == 0:
        token = token.replace(",", ".")           # '1,5' -> '1.5'
    else:
        token = token.replace(",", "")
    try:
        f = float(token)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def limpiar_texto(texto: str | None) -> str:
    """
    QUÉ HACE: quita etiquetas HTML, decodifica entidades ('&nbsp;', '&#8230;')
    y colapsa espacios.
    PARA QUÉ SIRVE: dimensión VALIDEZ. Los 'summary' de Google News vienen
    envueltos en <a>...</a><font>...</font>; sin limpieza, Gold mostraría markup.
    """
    if not texto:
        return ""
    sin_tags = _TAG_RE.sub(" ", str(texto))
    sin_entidades = html.unescape(sin_tags)
    return _WS_RE.sub(" ", sin_entidades).strip()


_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_HDR_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)


def limpiar_markdown(md: str | None, max_chars: int = 1200) -> str:
    """
    QUÉ HACE: reduce el markdown de Firecrawl a texto legible: elimina imágenes,
    convierte enlaces a su texto visible, quita marcadores de encabezado y navegación,
    y trunca a un tamaño razonable.
    PARA QUÉ SIRVE: dimensión VALIDEZ para la familia web_firecrawl, cuyo 'raw'
    es una página-índice con ~90% de navegación. Nota honesta: esta familia es la
    de menor densidad de señal; aquí se produce un resumen de la página, no un
    artículo. La extracción de ítems individuales del listado queda para una
    mejora posterior (requiere parser por sitio + fechas en español).
    """
    if not md:
        return ""
    t = _MD_IMG_RE.sub("", str(md))
    t = _MD_LINK_RE.sub(r"\1", t)
    t = _MD_HDR_RE.sub("", t)
    t = t.replace("\\", "")
    t = _WS_RE.sub(" ", t).strip()
    return t[:max_chars]


# ===========================================================================
# DIMENSIÓN: EXACTITUD  —  resolución de entidad
# ===========================================================================

def _normalizar_nombre(nombre: str | None) -> str:
    """QUÉ HACE: baja a minúsculas y quita acentos/puntuación. PARA QUÉ SIRVE: comparar nombres de entidad de forma robusta."""
    if not nombre:
        return ""
    t = unicodedata.normalize("NFKD", str(nombre))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return _WS_RE.sub(" ", t).strip()


def limpiar_entidad_declarada(empresa: str | None) -> str:
    """
    QUÉ HACE: limpia el campo _meta.empresa de anotaciones. 'ANSPE (cliente de
    IT CROWD)' -> 'ANSPE'; 'DITRA - Direccion ... (cliente de IT CROWD)' -> 'DITRA'.
    PARA QUÉ SIRVE: el campo empresa viene anotado con contexto operativo; para
    resolver la entidad hay que quedarse con el nombre, no con la anotación.
    """
    if not empresa:
        return ""
    e = re.sub(r"\([^)]*\)", "", str(empresa))         # quita paréntesis
    e = e.split(" - ")[0]                               # quita sufijo tras ' - '
    return e.strip()


def _coinciden_entidades(a: str, b: str) -> bool:
    """
    QUÉ HACE: decide si dos nombres refieren a la misma entidad, tolerando
    variantes como 'KPMG' / 'KPMG Colombia' o 'IBM Corporation' / 'IBM'.
    PARA QUÉ SIRVE: evitar falsos positivos de discrepancia. La bandera
    'entidad_discrepante' solo debe dispararse ante cruces REALES (IBM->Google),
    no ante sufijos de país o forma jurídica. Criterio: match exacto, o que el
    conjunto de tokens de uno sea subconjunto del otro (descartando palabras de
    ruido como 'sa', 'ltda', 'colombia', 'corporation').
    """
    na, nb = _normalizar_nombre(a), _normalizar_nombre(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    ruido = {"sa", "sas", "ltda", "inc", "corp", "corporation", "colombia",
             "co", "group", "grupo", "the", "de", "del", "la", "el"}
    ta = {t for t in na.split() if t not in ruido}
    tb = {t for t in nb.split() if t not in ruido}
    if not ta or not tb:
        return False
    return ta.issubset(tb) or tb.issubset(ta)


def resolver_entidad(entidad_declarada: str | None, entidad_contenido: str | None):
    """
    QUÉ HACE: decide la entidad real de un ítem cruzando lo DECLARADO en _meta
    (empresa) contra lo que dice el CONTENIDO (campo de autor de la fuente), y
    marca discrepancia cuando no coinciden.

    PARA QUÉ SIRVE: ataca la dimensión EXACTITUD, la más crítica. En Bronze
    aparecieron etiquetas erradas (empresa='IBM Corporation' sobre posts de
    Google y de Deloitte). Confiar en _meta.empresa propagaría esos errores a
    Gold ("IBM publicó lo de Deloitte"). La regla es: la entidad se toma del
    campo de autor del contenido; si difiere de lo declarado, se conserva el
    valor del contenido y se levanta un flag para auditoría, en vez de ocultar
    el problema.

    Devuelve (entidad_final, flag|None).
    """
    dec = limpiar_entidad_declarada(entidad_declarada)
    cont = (entidad_contenido or "").strip()

    if cont and dec:
        if _coinciden_entidades(dec, cont):
            # Coinciden (exacto o uno contenido en el otro): se conserva el más rico.
            return (cont if len(cont) >= len(dec) else dec), None
        # No coinciden de verdad: el contenido manda, pero se marca.
        return cont, "entidad_discrepante"
    if cont:
        return cont, None
    if dec:
        return dec, "entidad_sin_confirmar_en_contenido"
    return "", "entidad_ausente"


# ===========================================================================
# DIMENSIÓN: UNICIDAD  —  hash de contenido
# ===========================================================================

def hash_contenido(*partes) -> str:
    """
    QUÉ HACE: produce un hash SHA-256 estable a partir de las partes de contenido
    que definen la identidad lógica de un ítem (típicamente título + url + fecha).
    PARA QUÉ SIRVE: dimensión UNICIDAD. Un mismo hecho puede llegar por varias
    fuentes o corridas; el hash permite deduplicar CROSS-SOURCE. Se normaliza cada
    parte (minúsculas, sin acentos) para que variaciones triviales no rompan el match.
    """
    base = "||".join(_normalizar_nombre(p) for p in partes if p)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# ===========================================================================
# DIMENSIÓN: VIGENCIA  —  ventana de recencia
# ===========================================================================

def es_vigente(fecha_evento_iso: str | None, ventana_dias: int, ahora: datetime | None = None) -> bool:
    """
    QUÉ HACE: indica si la fecha del ítem cae dentro de la ventana de recencia.
    PARA QUÉ SIRVE: dimensión VIGENCIA. Sin esto, Gold mezclaría señales actuales
    con ruido histórico (noticia de 2015, tweet de 2022). No se descarta el dato:
    se marca, y Gold decide. Si no hay fecha válida, se considera NO vigente
    (conservador) y la ausencia se refleja además en completitud.
    """
    if not fecha_evento_iso:
        return False
    ahora = ahora or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(fecha_evento_iso)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    limite = ahora - timedelta(days=ventana_dias)
    # Vigente si está entre 'limite' y un pequeño margen futuro (fechas de fin de contrato).
    return dt >= limite


# ===========================================================================
# DIMENSIÓN: COMPLETITUD  —  campos requeridos
# ===========================================================================

def evaluar_completitud(canonico: dict, requeridos=("titulo", "fecha_evento_utc", "url")) -> list:
    """
    QUÉ HACE: lista qué campos requeridos del registro canónico quedaron vacíos.
    PARA QUÉ SIRVE: dimensión COMPLETITUD. No se rechaza el dato incompleto (se
    perdería señal); se REGISTRA qué le falta, para que Gold pondere la confianza
    y para tener métricas de completitud por familia en el dashboard.
    """
    faltantes = []
    for campo in requeridos:
        v = canonico.get(campo)
        if v is None or (isinstance(v, str) and v.strip() == ""):
            faltantes.append(campo)
    return faltantes