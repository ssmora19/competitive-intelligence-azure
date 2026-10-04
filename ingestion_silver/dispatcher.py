# -*- coding: utf-8 -*-
"""
dispatcher.py
=============

QUÉ HACE
--------
Dada la sección 'raw' de un blob de Bronze (y opcionalmente su 'fuente'),
determina a cuál de las 12 familias pertenece, inspeccionando la ESTRUCTURA del
dato (qué campos trae), no el nombre de la fuente.

PARA QUÉ SIRVE
--------------
Es la puerta de entrada de Silver: sin familia no hay normalizador. Se resuelve
por estructura por una razón sustentada con evidencia real: los nombres de
'fuente' resultaron inconsistentes ('patentes_tech' es RSS pero 'inversion_tech'
y 'documentacion_tecnica' son web). Detectar por forma del dato es robusto ante
nombres nuevos o mal puestos.

SUSTENTO (defensa)
------------------
"La familia se detecta por la firma estructural del registro, no por su nombre
de origen, precisamente porque los nombres de fuente demostraron ser poco
fiables. Esto hace el ruteo resistente a fuentes nuevas sin tocar código."
"""

from __future__ import annotations


def resolver_familia(raw: dict, fuente: str = "") -> str | None:
    """
    QUÉ HACE: devuelve el nombre de familia para un 'raw', o None si no reconoce
    ninguna firma estructural.
    PARA QUÉ SIRVE: enrutar cada blob a su normalizador. El orden de las pruebas
    va de las firmas más específicas a las más genéricas para evitar colisiones.
    """
    if not isinstance(raw, dict):
        return None
    k = raw.keys()

    # --- SECOP: se distinguen por campos exclusivos de cada dataset ---
    if "id_del_proceso" in k:
        return "secop_procesos"          # dataset p6dx-8zbt
    if "referencia_del_contrato" in k:
        return "secop_contratos"         # dataset jbjy-vk9h (contratos y adjudicatario)

    # --- LinkedIn Jobs: keyword de búsqueda + campos de vacante ---
    if "_keyword_buscada" in k or ("applyType" in k and "companyName" in k):
        return "linkedin_jobs"

    # --- LinkedIn post: URN de actividad ---
    if "activity_urn" in k or "full_urn" in k:
        return "linkedin_post"

    # --- YouTube Data API v3 ---
    if raw.get("kind") == "youtube#video" or ("snippet" in k and "contentDetails" in k):
        return "youtube"

    # --- GitHub API (repo) ---
    if "full_name" in k and "owner" in k and "stargazers_count" in k:
        return "github"

    # --- Redes vía Apify (cada actor con su firma) ---
    if "facebookUrl" in k or "pageName" in k:
        return "facebook"
    if "shortCode" in k or ("ownerUsername" in k and "displayUrl" in k):
        return "instagram"
    if "authorMeta" in k or ("webVideoUrl" in k and "musicMeta" in k):
        return "tiktok"
    if "twitterUrl" in k or ("full_text" in k and "conversation_id_str" in k):
        return "x"

    # --- web_firecrawl: markdown + metadata de scraping ---
    if "markdown" in k and "metadata" in k:
        return "web_firecrawl"

    # --- noticia_rss: firma de feed (título + link + summary + published) ---
    if "title" in k and "link" in k and "published" in k:
        return "noticia_rss"

    return None


def rol_entidad_secop(fuente: str) -> str:
    """
    QUÉ HACE: para SECOP contratos, indica si la entidad monitoreada actúa como
    'contratante' (emite el contrato) o 'proveedor' (lo gana).
    PARA QUÉ SIRVE: contratos y adjudicatario comparten estructura idéntica; solo
    el nombre de la fuente ('_adjudicatario') revela el rol. Sin esto, un contrato
    ganado se confundiría con uno emitido, invirtiendo el sentido de la señal.
    """
    return "proveedor" if "adjudicatario" in (fuente or "").lower() else "contratante"


def subforma_noticia(raw: dict) -> str:
    """
    QUÉ HACE: distingue las dos sub-formas de noticia_rss: 'google_news' (summary
    con HTML, link de redirect) vs 'rss_directo' (summary limpio, URL real).
    PARA QUÉ SIRVE: el normalizador de noticias ramifica según esto — en Google
    News hay que desenredar el HTML del summary; en RSS directo el texto y la URL
    se toman tal cual. El discriminador limpio es el prefijo de '_fuente_rss'.
    """
    fuente_rss = str(raw.get("_fuente_rss", ""))
    return "google_news" if fuente_rss.startswith("google_news") else "rss_directo"