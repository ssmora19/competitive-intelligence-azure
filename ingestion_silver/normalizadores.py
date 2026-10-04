# -*- coding: utf-8 -*-
"""
normalizadores.py
=================

QUÉ HACE
--------
Contiene un normalizador por cada una de las 12 familias. Cada uno toma el 'raw'
crudo de su familia y lo mapea a un conjunto de campos canónicos comunes
(título, descripción, fecha, url, entidad, idioma) más un bloque estructurado
propio de la familia (secop, empleo, social, repositorio, video, web).

PARA QUÉ SIRVE
--------------
Ataca la dimensión CONSISTENCIA: convierte 12 estructuras incompatibles en un
modelo único que Gold puede consumir sin saber de dónde vino cada dato. Cada
normalizador es DELGADO: solo mapea campos. Toda la lógica de calidad (fechas,
números, entidad, texto) se delega a calidad.py, para que viva en un solo lugar.

CONTRATO
--------
Cada función normaliza(raw, meta) -> dict con estas llaves:
  titulo, descripcion, fecha_evento_utc, url, idioma,
  entidad_declarada, entidad_contenido, termino_busqueda, origen_dato,
  bloque_nombre, bloque, hash_partes (tupla), flags (lista).
El pipeline se encarga de resolver la entidad, calcular calidad y ensamblar.
"""

from __future__ import annotations

from . import calidad
from . import dispatcher


# ---------------------------------------------------------------------------
# Utilidades locales de mapeo (no de calidad; solo extracción de campos)
# ---------------------------------------------------------------------------

def _titulo_desde_texto(texto: str | None, maximo: int = 140) -> str:
    """QUÉ HACE: deriva un título corto del cuerpo de un post. PARA QUÉ SIRVE: redes sin campo 'título' (FB, IG, TikTok, X, LinkedIn)."""
    limpio = calidad.limpiar_texto(texto)
    if not limpio:
        return ""
    primera = limpio.split("\n")[0].strip() if "\n" in limpio else limpio
    return primera[:maximo] + ("…" if len(primera) > maximo else "")


def _quitar_sufijo_fuente(titulo: str) -> str:
    """QUÉ HACE: quita el sufijo ' - Fuente' de titulares de Google News. PARA QUÉ SIRVE: título limpio sin el nombre del medio."""
    if " - " in titulo:
        return titulo.rsplit(" - ", 1)[0].strip()
    return titulo.strip()


def _base_contenido():
    """QUÉ HACE: plantilla de retorno con todas las llaves del contrato. PARA QUÉ SIRVE: garantizar que ningún normalizador olvide una llave."""
    return {
        "titulo": "", "descripcion": "", "fecha_evento_utc": None, "url": "", "idioma": None,
        "entidad_declarada": "", "entidad_contenido": "", "termino_busqueda": None, "origen_dato": None,
        "bloque_nombre": None, "bloque": None, "hash_partes": (), "flags": [],
    }


# ===========================================================================
# SECOP
# ===========================================================================

def norm_secop_procesos(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza un PROCESO de SECOP (dataset p6dx-8zbt). PARA QUÉ SIRVE: señal de contratación en fase de licitación abierta (oportunidad para TAK)."""
    r = _base_contenido()
    r["titulo"] = calidad.limpiar_texto(raw.get("descripci_n_del_procedimiento"))
    r["descripcion"] = r["titulo"]
    r["fecha_evento_utc"] = calidad.parse_fecha(raw.get("fecha_de_publicacion_del") or raw.get("fecha_de_ultima_publicaci"))
    r["url"] = (raw.get("urlproceso") or {}).get("url", "")
    r["entidad_declarada"] = meta.get("empresa", "")
    r["entidad_contenido"] = raw.get("entidad", "")
    r["bloque_nombre"] = "secop"
    r["bloque"] = {
        "id_secop": raw.get("id_del_proceso"),
        "rol_entidad": "contratante",
        "estado": raw.get("estado_resumen") or raw.get("estado_del_procedimiento"),
        "modalidad": raw.get("modalidad_de_contratacion"),
        "tipo_contrato": raw.get("tipo_de_contrato"),
        "valor": calidad.a_numero(raw.get("precio_base")),
        "entidad": raw.get("entidad"),
        "ubicacion": " / ".join(x for x in [raw.get("departamento_entidad"), raw.get("ciudad_entidad")] if x),
        # --- NUEVOS: para licitaciones abiertas (oportunidades) ---
        "objeto": calidad.limpiar_texto(raw.get("descripci_n_del_procedimiento")),
        "unspsc": raw.get("codigo_principal_de_categoria"),
        "adjudicado": raw.get("adjudicado"),                       # No = licitación libre
        "fase": raw.get("fase"),
        "fecha_publicacion": calidad.parse_fecha(raw.get("fecha_de_publicacion_del")),
        "duracion": raw.get("duracion"),
        "unidad_duracion": raw.get("unidad_de_duracion"),
        "proveedores_manifestaron": raw.get("proveedores_que_manifestaron"),
    }
    r["hash_partes"] = (raw.get("id_del_proceso"), r["url"])
    return r


def norm_secop_contratos(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza un CONTRATO de SECOP (dataset jbjy-vk9h), sea emitido o ganado. PARA QUÉ SIRVE: señal de contratación adjudicada; el rol distingue si la entidad contrata o gana."""
    r = _base_contenido()
    rol = dispatcher.rol_entidad_secop(meta.get("fuente", ""))
    objeto = raw.get("objeto_del_contrato")
    if not objeto or objeto.lower() in ("no definido", "sin descripcion"):
        objeto = raw.get("descripcion_del_proceso")
    r["titulo"] = calidad.limpiar_texto(objeto)
    r["descripcion"] = calidad.limpiar_texto(raw.get("descripcion_del_proceso") or objeto)
    r["fecha_evento_utc"] = calidad.parse_fecha(raw.get("fecha_de_fin_del_contrato"))
    r["url"] = (raw.get("urlproceso") or {}).get("url", "")
    r["entidad_declarada"] = meta.get("empresa", "")
    # La entidad monitoreada según su rol: contratante = quien emite; proveedor = quien gana.
    r["entidad_contenido"] = raw.get("proveedor_adjudicado") if rol == "proveedor" else raw.get("nombre_entidad")
    r["bloque_nombre"] = "secop"
    r["bloque"] = {
        "id_secop": raw.get("referencia_del_contrato"),
        "rol_entidad": rol,
        "estado": raw.get("estado_contrato"),
        "tipo_contrato": raw.get("tipo_de_contrato"),
        "modalidad": raw.get("modalidad_de_contratacion"),
        "valor": calidad.a_numero(raw.get("valor_del_contrato")),
        "entidad_contratante": raw.get("nombre_entidad"),
        "proveedor_adjudicado": raw.get("proveedor_adjudicado"),
        "nit_entidad": raw.get("nit_entidad"),
        "ubicacion": " / ".join(x for x in [raw.get("departamento"), raw.get("ciudad")] if x),
        # --- NUEVOS: objeto, clasificación UNSPSC ---
        "objeto": calidad.limpiar_texto(objeto),
        "unspsc": raw.get("codigo_de_categoria_principal"),
        # --- NUEVOS: fechas completas y duración (para línea de tiempo y vencimientos) ---
        "fecha_firma": calidad.parse_fecha(raw.get("fecha_de_firma")),
        "fecha_inicio": calidad.parse_fecha(raw.get("fecha_de_inicio_del_contrato")),
        "fecha_fin": calidad.parse_fecha(raw.get("fecha_de_fin_del_contrato")),
        "duracion": raw.get("duraci_n_del_contrato"),
        "prorrogable": raw.get("el_contrato_puede_ser_prorrogado"),
        "dias_adicionados": raw.get("dias_adicionados"),
        "liquidado": raw.get("liquidaci_n"),
        # --- NUEVOS: financiero (estado real de ejecución) ---
        "valor_pagado": calidad.a_numero(raw.get("valor_pagado")),
        "valor_facturado": calidad.a_numero(raw.get("valor_facturado")),
        "valor_pendiente_ejecucion": calidad.a_numero(raw.get("valor_pendiente_de_ejecucion")),
        "valor_pendiente_pago": calidad.a_numero(raw.get("valor_pendiente_de_pago")),
        # --- NUEVOS: contexto ---
        "sector": raw.get("sector"),
        "rama": raw.get("rama"),
        "origen_recursos": raw.get("origen_de_los_recursos"),
    }
    r["hash_partes"] = (raw.get("referencia_del_contrato"), r["url"])
    return r

# ===========================================================================
# NOTICIA (RSS) — dos sub-formas
# ===========================================================================

def norm_noticia_rss(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza una noticia de feed, ramificando entre Google News y RSS directo. PARA QUÉ SIRVE: señal de contexto/mercado; las dos sub-formas traen el summary y el link de forma distinta."""
    r = _base_contenido()
    sub = dispatcher.subforma_noticia(raw)
    r["titulo"] = _quitar_sufijo_fuente(calidad.limpiar_texto(raw.get("title")))
    r["descripcion"] = calidad.limpiar_texto(raw.get("summary"))
    r["fecha_evento_utc"] = calidad.parse_fecha(raw.get("published"))
    r["url"] = raw.get("link", "")
    r["entidad_declarada"] = meta.get("empresa", "")
    # En feeds no hay 'autor-entidad' fiable; _entidad_buscada suele ser una CONSULTA.
    termino = raw.get("_entidad_buscada")
    r["termino_busqueda"] = termino
    r["origen_dato"] = str(raw.get("_fuente_rss", "")).replace("google_news:", "").strip() or None
    r["flags"] = [f"subforma_{sub}"]
    if sub == "google_news":
        r["flags"].append("url_redirect_google_news")  # el link es un wrapper, no la URL final
    r["hash_partes"] = (r["titulo"], r["fecha_evento_utc"])
    return r


# ===========================================================================
# WEB (Firecrawl) — páginas-índice
# ===========================================================================

def norm_web_firecrawl(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza una página scrapeada (markdown+metadata) como UN documento-resumen. PARA QUÉ SIRVE: familia de menor densidad de señal; se resume la página. La extracción de ítems del listado queda como mejora futura (flag)."""
    r = _base_contenido()
    md = raw.get("metadata") or {}
    r["titulo"] = calidad.limpiar_texto(md.get("title"))
    r["descripcion"] = calidad.limpiar_texto(md.get("description")) or calidad.limpiar_markdown(raw.get("markdown"))
    # Fecha: si la página declara una, se usa; si no, se cae al ingest (aprox.).
    r["fecha_evento_utc"] = (
        calidad.parse_fecha(md.get("updated_at"))
        or calidad.parse_fecha(md.get("ms.date"))
        or calidad.parse_fecha(meta.get("ingest_ts_utc"))
    )
    r["url"] = md.get("sourceURL") or raw.get("_url_scrapeada") or md.get("url") or ""
    r["idioma"] = md.get("language")
    r["origen_dato"] = meta.get("empresa", "")   # aquí 'empresa' es etiqueta de fuente, no entidad
    # La entidad, si el sitio la declara (Author/Publisher), se aprovecha; si no, queda vacía.
    r["entidad_contenido"] = md.get("Publisher") or md.get("Author") or ""
    r["entidad_declarada"] = ""  # web no monitorea una entidad; no forzamos empresa como entidad
    r["bloque_nombre"] = "web"
    r["bloque"] = {
        "status_code": md.get("statusCode"),
        "content_type": md.get("contentType"),
        "es_pagina_indice": True,
    }
    r["flags"] = ["web_pagina_indice_no_articulo"]
    r["hash_partes"] = (r["titulo"], r["url"])
    return r


# ===========================================================================
# LINKEDIN
# ===========================================================================

def norm_linkedin_post(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza un post de LinkedIn. PARA QUÉ SIRVE: contenido corporativo del competidor; la entidad se toma del autor real, no de _meta.empresa."""
    r = _base_contenido()
    autor = raw.get("author") or {}
    posted = raw.get("posted_at") or {}
    r["titulo"] = _titulo_desde_texto(raw.get("text"))
    r["descripcion"] = calidad.limpiar_texto(raw.get("text"))
    r["fecha_evento_utc"] = calidad.parse_fecha(posted.get("date") or posted.get("timestamp"))
    r["url"] = raw.get("post_url", "")
    r["idioma"] = raw.get("post_language_code")
    r["entidad_declarada"] = meta.get("empresa", "")
    r["entidad_contenido"] = autor.get("name", "")
    st = raw.get("stats") or {}
    r["bloque_nombre"] = "social"
    r["bloque"] = {
        "red": "linkedin",
        "autor": autor.get("name"),
        "seguidores_autor": autor.get("follower_count"),
        "tipo_post": raw.get("post_type"),
        "reacciones": st.get("total_reactions"),
        "comentarios": st.get("comments"),
        "compartidos": st.get("reposts"),
    }
    r["hash_partes"] = (raw.get("full_urn") or raw.get("activity_urn"), r["url"])
    return r


def norm_linkedin_jobs(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza una vacante de LinkedIn Jobs. PARA QUÉ SIRVE: señal de demanda de talento del mercado; la keyword de búsqueda es una consulta (ruidosa), no una entidad."""
    r = _base_contenido()
    r["titulo"] = calidad.limpiar_texto(raw.get("title"))
    r["descripcion"] = calidad.limpiar_texto(raw.get("description"))
    r["fecha_evento_utc"] = calidad.parse_fecha(raw.get("postedDate"))
    r["url"] = raw.get("url", "")
    r["entidad_declarada"] = meta.get("empresa", "")
    r["entidad_contenido"] = raw.get("companyName", "")
    r["termino_busqueda"] = raw.get("_keyword_buscada")
    r["bloque_nombre"] = "empleo"
    r["bloque"] = {
        "cargo": raw.get("title"),
        "empresa_contratante": raw.get("companyName"),
        "sector": raw.get("sector"),
        "seniority": raw.get("experienceLevel"),
        "tipo_contrato": raw.get("contractType"),
        "ubicacion": raw.get("location"),
        "n_aplicantes": calidad.a_numero(raw.get("applicationsCount")),
    }
    r["hash_partes"] = (raw.get("id"), r["url"])
    return r


# ===========================================================================
# YOUTUBE
# ===========================================================================

def _iso8601_duracion_a_segundos(dur: str | None):
    """QUÉ HACE: convierte 'PT10M10S' a segundos. PARA QUÉ SIRVE: YouTube entrega la duración en ISO 8601; Gold necesita segundos numéricos."""
    if not dur:
        return None
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur)
    if not m:
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def norm_youtube(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza un video de YouTube. PARA QUÉ SIRVE: contenido audiovisual del competidor; entidad = canal, contadores como números."""
    r = _base_contenido()
    sn = raw.get("snippet") or {}
    cd = raw.get("contentDetails") or {}
    stt = raw.get("statistics") or {}
    r["titulo"] = calidad.limpiar_texto(sn.get("title"))
    r["descripcion"] = calidad.limpiar_texto(sn.get("description"))
    r["fecha_evento_utc"] = calidad.parse_fecha(sn.get("publishedAt"))
    r["url"] = f"https://www.youtube.com/watch?v={raw.get('id')}" if raw.get("id") else ""
    r["idioma"] = sn.get("defaultLanguage") or sn.get("defaultAudioLanguage")
    r["entidad_declarada"] = meta.get("empresa", "")
    r["entidad_contenido"] = sn.get("channelTitle", "")
    r["bloque_nombre"] = "video"
    r["bloque"] = {
        "canal": sn.get("channelTitle"),
        "duracion_seg": _iso8601_duracion_a_segundos(cd.get("duration")),
        "vistas": calidad.a_numero(stt.get("viewCount")),
        "comentarios": calidad.a_numero(stt.get("commentCount")),
    }
    r["hash_partes"] = (raw.get("id"), r["url"])
    return r


# ===========================================================================
# GITHUB
# ===========================================================================

def norm_github(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza un repositorio de GitHub, conservando ~10 campos útiles de los ~90. PARA QUÉ SIRVE: actividad técnica; búsqueda por topic es muy ruidosa, así que el topic es 'término de búsqueda', no entidad."""
    r = _base_contenido()
    owner = raw.get("owner") or {}
    lic = raw.get("license") or {}
    r["titulo"] = raw.get("full_name") or raw.get("name") or ""
    r["descripcion"] = calidad.limpiar_texto(raw.get("description"))
    r["fecha_evento_utc"] = calidad.parse_fecha(raw.get("pushed_at") or raw.get("updated_at"))
    r["url"] = raw.get("html_url", "")
    r["idioma"] = raw.get("language")  # lenguaje de programación (no idioma humano)
    r["entidad_declarada"] = meta.get("empresa", "")
    r["entidad_contenido"] = owner.get("login", "")
    r["termino_busqueda"] = raw.get("_topic_buscado")
    r["bloque_nombre"] = "repositorio"
    r["bloque"] = {
        "owner": owner.get("login"),
        "lenguaje": raw.get("language"),
        "stars": calidad.a_numero(raw.get("stargazers_count")),
        "forks": calidad.a_numero(raw.get("forks_count")),
        "licencia": lic.get("spdx_id"),
        "topics": raw.get("topics") or [],
        "creado": calidad.parse_fecha(raw.get("created_at")),
    }
    r["hash_partes"] = (raw.get("full_name"), r["url"])
    return r


# ===========================================================================
# REDES (Apify)  —  facebook, instagram, tiktok, x
# ===========================================================================

def norm_facebook(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza un post de Facebook (Apify). PARA QUÉ SIRVE: contenido social; entidad = página real, no _meta.empresa."""
    r = _base_contenido()
    user = raw.get("user") or {}
    r["titulo"] = _titulo_desde_texto(raw.get("text"))
    r["descripcion"] = calidad.limpiar_texto(raw.get("text"))
    r["fecha_evento_utc"] = calidad.parse_fecha(raw.get("time") or raw.get("timestamp"))
    r["url"] = raw.get("topLevelUrl") or raw.get("url", "")
    r["entidad_declarada"] = meta.get("empresa", "")
    r["entidad_contenido"] = user.get("name") or raw.get("pageName") or ""
    r["bloque_nombre"] = "social"
    r["bloque"] = {
        "red": "facebook", "pagina": raw.get("pageName"),
        "likes": calidad.a_numero(raw.get("likes")),
        "compartidos": calidad.a_numero(raw.get("shares")),
    }
    r["hash_partes"] = (raw.get("postId"), r["url"])
    return r


def norm_instagram(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza un post de Instagram (Apify). PARA QUÉ SIRVE: contenido social; entidad = cuenta dueña del post."""
    r = _base_contenido()
    r["titulo"] = _titulo_desde_texto(raw.get("caption"))
    r["descripcion"] = calidad.limpiar_texto(raw.get("caption"))
    r["fecha_evento_utc"] = calidad.parse_fecha(raw.get("timestamp"))
    r["url"] = raw.get("url", "")
    r["entidad_declarada"] = meta.get("empresa", "")
    r["entidad_contenido"] = raw.get("ownerFullName") or raw.get("ownerUsername") or ""
    r["bloque_nombre"] = "social"
    r["bloque"] = {
        "red": "instagram", "cuenta": raw.get("ownerUsername"),
        "likes": calidad.a_numero(raw.get("likesCount")),
        "comentarios": calidad.a_numero(raw.get("commentsCount")),
        "hashtags": raw.get("hashtags") or [],
    }
    r["hash_partes"] = (raw.get("id") or raw.get("shortCode"), r["url"])
    return r


def norm_tiktok(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza un video de TikTok (Apify). PARA QUÉ SIRVE: contenido social; entidad = autor, fecha desde epoch/ISO."""
    r = _base_contenido()
    am = raw.get("authorMeta") or {}
    r["titulo"] = _titulo_desde_texto(raw.get("text"))
    r["descripcion"] = calidad.limpiar_texto(raw.get("text"))
    r["fecha_evento_utc"] = calidad.parse_fecha(raw.get("createTimeISO") or raw.get("createTime"))
    r["url"] = raw.get("webVideoUrl", "")
    r["idioma"] = raw.get("textLanguage")
    r["entidad_declarada"] = meta.get("empresa", "")
    r["entidad_contenido"] = am.get("nickName") or am.get("name") or ""
    r["bloque_nombre"] = "social"
    r["bloque"] = {
        "red": "tiktok", "cuenta": am.get("name"),
        "reproducciones": calidad.a_numero(raw.get("playCount")),
        "likes": calidad.a_numero(raw.get("diggCount")),
        "comentarios": calidad.a_numero(raw.get("commentCount")),
        "compartidos": calidad.a_numero(raw.get("shareCount")),
    }
    r["hash_partes"] = (raw.get("id"), r["url"])
    return r


def norm_x(raw: dict, meta: dict) -> dict:
    """QUÉ HACE: normaliza un tweet/post de X (Apify). PARA QUÉ SIRVE: contenido social; entidad = cuenta autora, fecha en formato Twitter."""
    r = _base_contenido()
    autor = raw.get("author") or {}
    r["titulo"] = _titulo_desde_texto(raw.get("full_text"))
    r["descripcion"] = calidad.limpiar_texto(raw.get("full_text"))
    r["fecha_evento_utc"] = calidad.parse_fecha(raw.get("created_at"))
    r["url"] = raw.get("url") or raw.get("twitterUrl", "")
    r["idioma"] = raw.get("lang")
    r["entidad_declarada"] = meta.get("empresa", "")
    r["entidad_contenido"] = autor.get("name") or autor.get("screen_name") or ""
    r["bloque_nombre"] = "social"
    r["bloque"] = {
        "red": "x", "cuenta": autor.get("screen_name"),
        "favoritos": calidad.a_numero(raw.get("favorite_count")),
        "retweets": calidad.a_numero(raw.get("retweet_count")),
        "respuestas": calidad.a_numero(raw.get("reply_count")),
    }
    r["hash_partes"] = (raw.get("id"), r["url"])
    return r


# ---------------------------------------------------------------------------
# REGISTRO: familia -> normalizador
# ---------------------------------------------------------------------------
# El pipeline usa este diccionario para despachar sin condicionales gigantes.
REGISTRO = {
    "secop_procesos": norm_secop_procesos,
    "secop_contratos": norm_secop_contratos,
    "noticia_rss": norm_noticia_rss,
    "web_firecrawl": norm_web_firecrawl,
    "linkedin_post": norm_linkedin_post,
    "linkedin_jobs": norm_linkedin_jobs,
    "youtube": norm_youtube,
    "github": norm_github,
    "facebook": norm_facebook,
    "instagram": norm_instagram,
    "tiktok": norm_tiktok,
    "x": norm_x,
}