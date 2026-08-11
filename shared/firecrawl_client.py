"""
shared/firecrawl_client.py
scraper para paginas web

Reemplaza al actor apify~website-content-crawler.

Ventajas sobre Apify:
  - Maneja JavaScript y SPAs (React, Angular, Vue)
  - Bypass de protecciones anti-bot (Cloudflare, etc.)
  - 500 páginas/mes gratis
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
            "Authorization": f"Bearer {self._key}", #la credencial de acceso a la API de Firecrawl
            "Content-Type":  "application/json", #el tipo de contenido a enviar
        }

    def scrape(self, url: str) -> Optional[dict]:
        """
        Extrae el contenido de una URL individual.

        Retorna dict con:
          - markdown:  contenido limpio
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
                    "formats": ["markdown"],   # solo markdown, contenido limpio
                    "onlyMainContent": True,   # descarta nav, footer, banners
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("success"): #para que no se rompa el flujo si falla un scrapeo, se loguea y se retorna None 
                log.warning("Firecrawl: sin éxito para %s — %s", url, data.get("error"))
                return None

            resultado = data.get("data", {})

            # ── Filtro de calidad: descartar respuestas que NO son 200 ──
            # Firecrawl pone el código HTTP real en metadata.statusCode.
            # Las páginas que no existen (404) igual devuelven success=True
            # con un markdown de "página no encontrada"; este filtro evita
            # que esa basura entre a Bronze. Aplica a TODAS las fuentes web
            # (web, noticias, benchmarking) porque todas pasan por aquí.
            status = (resultado.get("metadata") or {}).get("statusCode")
            if status is not None and int(status) != 200:
                log.warning(
                    "Firecrawl: statusCode %s (no 200) para %s — descartado",
                    status, url,
                )
                return None

            log.info(
                "Firecrawl: %s → %d chars markdown (status %s)",
                url, len(resultado.get("markdown") or ""), status,
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
        Consulta el uso actual de créditos, para monitorear que no se agoten los 500.
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
# web_paths es la lista de rutas que se van a mirar de cualquier empresa.
#
# Lista RECORTADA: solo rutas que responden a un KIN de competidores y que
# la web es la ÚNICA fuente capaz de dar (portafolio y alianzas).
#   ""          home          -> posicionamiento / propuesta de valor  (KIN 2, 3)
#   /servicios  /services     -> portafolio de servicios               (KIN 1, 2)
#   /soluciones               -> soluciones por industria              (KIN 1, 2)
#   /partners   /socios       -> alianzas / fabricantes                (KIN 4)
#   /tecnologia               -> stack tecnológico y partners          (KIN 4)
#
# Quitadas a propósito:
#   /noticias /news /blog     -> ahora vía Google News (gratis, sin 404)
#   /casos-de-exito /case-studies /clientes -> nombres muy variables,
#                                generaban la mayoría de los 404 y aportan poco.
WEB_PATHS = [
    "",                  # home — descripción general, propuesta de valor
    "/servicios",        # servicios principales
    "/services",         # versión en inglés
    "/soluciones",       # soluciones por industria
    "/partners",         # alianzas / fabricantes
    "/socios",           # alianzas (español)
    "/tecnologia",       # stack tecnológico y partners
]


def build_company_urls(website: str) -> list[str]:
    """
    Construye la lista de URLs a crawlear para una empresa.
    Descarta duplicados si el sitio no tiene ese path.
    """
    base = website.rstrip("/")
    return [f"{base}{path}" for path in WEB_PATHS]