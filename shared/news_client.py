"""
shared/news_client.py
=====================
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

Relevancia para TAK:
  Detecta tendencias antes de que lleguen al mercado colombiano.
  Especialmente útil para Oracle, Snowflake y cloud que son el core de TAK.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Optional

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

    def throttle(self) -> None:
        time.sleep(self._throttle)
