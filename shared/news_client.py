"""
shared/news_client.py

Cliente para RSS feeds de noticias tech — tendencias tecnológicas.

Usa feedparser para leer RSS/Atom feeds — completamente gratuito.
Sin API key requerida.

Fuentes configuradas:
  - TechCrunch (tecnología general)
  - InfoQ (arquitectura, cloud, data)
  - The New Stack (Kubernetes, cloud native)
  - Oracle Blog (novedades Oracle)
  - Snowflake Blog (novedades Snowflake)
  - Red Hat Blog (novedades Red Hat)
  - Platzi Blog (tech Colombia/Latam)
  - MinTIC noticias (tech Colombia)

NUEVO — buscar_noticias_empresa():
  Google News RSS por nombre de entidad (Camino A). Reemplaza el scraping
  de rutas /news que producía 404. Lo usan clientes y competidores.

Relevancia para TAK:
  Detecta tendencias antes de que lleguen al mercado colombiano.
  Especialmente útil para Oracle, Snowflake y cloud que son el core de TAK.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote_plus

log = logging.getLogger("ci.news")

# RSS feeds relevantes para TAK — todos gratuitos
RSS_FEEDS = {
    # Fabricantes core de TAK — solo feeds confirmados activos
    "oracle_blog":     "https://blogs.oracle.com/oracle-database/rss",
    "snowflake_blog":  "https://medium.com/feed/snowflake-engineering",
    "redhat_blog":     "https://www.redhat.com/en/rss/blog",

    # Tech general
    "techcrunch":      "https://techcrunch.com/feed/",
    "infoq":           "https://feed.infoq.com/",
    "thenewstack":     "https://thenewstack.io/feed/",

    # Tech Colombia/Latam
    "enter_co":        "https://www.enter.co/feed/",
    "itnow_latam":     "https://itnow.com.co/feed/",
}

DEFAULT_THROTTLE = 1


class NewsClient:
    """
    Cliente para RSS feeds de noticias tech.

    Uso:
        client   = NewsClient()
        noticias = client.get_feed("oracle_blog", limite=10)
        todas    = client.get_all_feeds(limite=5)
        prensa   = client.buscar_noticias_empresa("Ecopetrol", limite=10)
    """

    def __init__(self, throttle: int = DEFAULT_THROTTLE):
        self._throttle = throttle
        self._feedparser = None

    def _get_feedparser(self):
        """Lazy import de feedparser."""
        if self._feedparser is None:
            try:
                import feedparser
                self._feedparser = feedparser
            except ImportError:
                log.error("feedparser no instalado — ejecuta: pip install feedparser")
                return None
        return self._feedparser

    def get_feed(
        self,
        fuente: str,
        limite: int = 10,
    ) -> list[dict]:
        """
        Lee un RSS feed y retorna los items como dicts crudos.

        fuente: clave del diccionario RSS_FEEDS o URL directa
        """
        fp = self._get_feedparser()
        if not fp:
            return []

        url = RSS_FEEDS.get(fuente, fuente)

        try:
            log.info("RSS: leyendo %s (%s)", fuente, url)
            feed = fp.parse(url)

            if feed.bozo and not feed.entries:
                log.warning("RSS: feed mal formado o vacío — %s", url)
                return []

            items = []
            for entry in feed.entries[:limite]:
                item = {
                    "title":        entry.get("title", ""),
                    "link":         entry.get("link", ""),
                    "summary":      entry.get("summary", "")[:2000],
                    "published":    entry.get("published", ""),
                    "author":       entry.get("author", ""),
                    "tags":         [t.get("term", "") for t in entry.get("tags", [])],
                    "_fuente_rss":  fuente,
                    "_url_feed":    url,
                    "_ingest_ts":   datetime.now(timezone.utc).isoformat(),
                }
                items.append(item)

            log.info("RSS: %d items de %s", len(items), fuente)
            return items

        except Exception as ex:
            log.error("RSS error [%s]: %s", fuente, ex)
            return []

    def get_all_feeds(self, limite: int = 5) -> list[dict]:
        """
        Lee todos los feeds configurados.
        Retorna lista consolidada con metadato _fuente_rss.
        """
        todos = []
        for fuente in RSS_FEEDS:
            items = self.get_feed(fuente, limite)
            todos.extend(items)
            time.sleep(self._throttle)
        return todos

    def get_feeds_by_group(
        self,
        grupo: str,
        limite: int = 5,
    ) -> list[dict]:
        """
        Lee feeds de un grupo específico.
        grupos: 'fabricantes' | 'tech_general' | 'colombia'
        """
        grupos = {
            "fabricantes":   ["oracle_blog", "snowflake_blog", "redhat_blog"],
            "tech_general":  ["techcrunch", "infoq", "thenewstack"],
            "colombia":      ["enter_co", "itnow_latam"],
        }

        fuentes = grupos.get(grupo, [])
        todos   = []
        for fuente in fuentes:
            items = self.get_feed(fuente, limite)
            todos.extend(items)
            time.sleep(self._throttle)
        return todos

    # ─────────────────────────────────────────────────────────────
    # NUEVO — Google News por nombre de entidad (Camino A)
    # ─────────────────────────────────────────────────────────────
    def buscar_noticias_empresa(
        self,
        nombre_empresa: str,
        limite: int = 10,
        region: str = "CO",
        idioma: str = "es-419",
    ) -> list[dict]:
        """
        QUÉ HACE:
          Arma el feed RSS de búsqueda de Google News para nombre_empresa,
          filtrado por región (Colombia) e idioma, y lo lee con get_feed.

        PARA QUÉ SIRVE:
          Traer noticias de TERCEROS sobre un competidor o cliente SIN
          scrapear su web (evita los 404 de rutas adivinadas). Gratis.
          Reutiliza get_feed, que ya sabe leer y limpiar RSS.

        Ejemplo:
          buscar_noticias_empresa("Ecopetrol") ->
          https://news.google.com/rss/search?q=Ecopetrol&hl=es-419&gl=CO&ceid=CO:es
        """
        if not nombre_empresa or not nombre_empresa.strip():
            return []

        query        = quote_plus(nombre_empresa.strip())
        idioma_corto = idioma.split("-")[0]
        url = (
            f"https://news.google.com/rss/search?q={query}"
            f"&hl={idioma}&gl={region}&ceid={region}:{idioma_corto}"
        )

        items = self.get_feed(url, limite=limite)
        # Reetiquetar la trazabilidad: por qué entidad se buscó
        for it in items:
            it["_fuente_rss"]      = f"google_news:{nombre_empresa}"
            it["_entidad_buscada"] = nombre_empresa
        return items

    def throttle(self) -> None:
        time.sleep(self._throttle)