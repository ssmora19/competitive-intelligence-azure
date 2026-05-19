"""
shared/firecrawl_client.py
==========================
Cliente para Firecrawl — web scraping para la capa Bronze.

Reemplaza al actor apify~website-content-crawler.

Ventajas sobre Apify para web:
  - Maneja JavaScript y SPAs (React, Angular, Vue)
  - Bypass de protecciones anti-bot (Cloudflare, etc.)
  - Retorna Markdown limpio — ideal para Silver con IA
  - 500 páginas/mes gratis — suficiente para 29 empresas semanalmente
  - API key propia → trazable en local.settings.json

Plan gratuito:
  500 páginas/mes → 29 empresas × ~6 paths = ~174 páginas/semana
  Estás dentro del límite con margen.
"""

import os
import time
import logging
from typing import Optional

import requests

log = logging.getLogger("ci.firecrawl")

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_BASE    = "https://api.firecrawl.dev/v1"
DEFAULT_THROTTLE  = 2  # segundos entre requests para no saturar


class FirecrawlClient:
    """
    Cliente para Firecrawl API.

    Uso:
        client = FirecrawlClient()
        resultado = client.scrape("https://www.accenture.com/co-es")
        markdown  = resultado.get("markdown", "")
    """

    def __init__(self, api_key: str = None, throttle: int = DEFAULT_THROTTLE):
        self._key      = api_key or FIRECRAWL_API_KEY
        self._throttle = throttle
        self._headers  = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type":  "application/json",
        }

    def scrape(self, url: str) -> Optional[dict]:
        """
        Extrae el contenido de una URL individual.

        Retorna dict con:
          - markdown:  contenido limpio en Markdown
          - metadata:  título, descripción, og tags, etc.
          - html:      HTML limpio (opcional)

        Retorna None si falla.
        """
        try:
            log.info("Firecrawl: scraping %s", url)
            resp = requests.post(
                f"{FIRECRAWL_BASE}/scrape",
                headers=self._headers,
                json={
                    "url":     url,
                    "formats": ["markdown"],   # solo markdown — suficiente para Bronze
                    "onlyMainContent": True,   # descarta nav, footer, banners
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("success"):
                log.warning("Firecrawl: sin éxito para %s — %s", url, data.get("error"))
                return None

            resultado = data.get("data", {})
            log.info(
                "Firecrawl: %s → %d chars markdown",
                url, len(resultado.get("markdown") or "")
            )
            return resultado

        except requests.HTTPError as ex:
            log.error("Firecrawl HTTP error [%s] %s: %s",
                      url, ex.response.status_code, ex.response.text[:200])
            return None
        except requests.Timeout:
            log.error("Firecrawl timeout [%s]", url)
            return None
        except Exception as ex:
            log.error("Firecrawl error [%s]: %s", url, ex)
            return None

    def scrape_many(self, urls: list[str]) -> list[dict]:
        """
        Extrae contenido de múltiples URLs secuencialmente con throttle.

        Retorna lista de dicts — cada uno incluye _url para trazabilidad.
        Los que fallaron no se incluyen en el resultado.
        """
        resultados = []
        for url in urls:
            resultado = self.scrape(url)
            if resultado:
                resultado["_url_scrapeada"] = url  # metadato de ingestión
                resultados.append(resultado)
            time.sleep(self._throttle)
        return resultados

    def throttle(self) -> None:
        time.sleep(self._throttle)

    def check_credits(self) -> Optional[dict]:
        """
        Consulta el uso actual de créditos.
        Útil para monitorear que no se agoten los 500 del plan gratis.
        """
        try:
            resp = requests.get(
                f"{FIRECRAWL_BASE}/team/usage",
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as ex:
            log.error("Firecrawl credits error: %s", ex)
            return None


# ─────────────────────────────────────────────────────────────
# PATHS A CRAWLEAR POR EMPRESA
# ─────────────────────────────────────────────────────────────
# Centralizado aquí para que function_competidores.py
# no tenga que saber qué paths crawlear.

WEB_PATHS = [
    "",                  # home — descripción general, propuesta de valor
    "/servicios",        # servicios principales
    "/soluciones",       # soluciones por industria
    "/services",         # versión en inglés
    "/noticias",         # noticias y comunicados
    "/news",
    "/blog",
    "/casos-de-exito",   # casos de éxito
    "/clientes",
    "/case-studies",
    "/tecnologia",       # stack tecnológico y partners
    "/partners",
    "/socios",
]


def build_company_urls(website: str) -> list[str]:
    """
    Construye la lista de URLs a crawlear para una empresa.
    Descarta duplicados si el sitio no tiene ese path.
    """
    base = website.rstrip("/")
    return [f"{base}{path}" for path in WEB_PATHS]
