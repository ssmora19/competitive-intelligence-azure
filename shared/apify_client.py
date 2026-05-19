"""
shared/apify_client.py
======================
Cliente centralizado para Apify.

Un solo lugar para:
  - ejecutar actores
  - obtener datasets
  - manejar errores y timeouts
  - throttling entre llamadas

Así ninguna function hace requests directos a Apify.
"""

import os
import time
import logging
from typing import Optional

import requests

log = logging.getLogger("ci.apify")

APIFY_TOKEN       = os.getenv("APIFY_TOKEN", "")
DEFAULT_WAIT_SEC  = 120   # tiempo máximo esperando resultado del actor
DEFAULT_THROTTLE  = 2     # segundos entre llamadas por empresa


class ApifyClient:
    """
    Cliente reutilizable para Apify Actor Runs API.

    Uso:
        client = ApifyClient()
        items  = client.run_actor("apify~instagram-scraper", payload)
    """

    def __init__(
        self,
        token:     str = None,
        wait_sec:  int = DEFAULT_WAIT_SEC,
        throttle:  int = DEFAULT_THROTTLE,
    ):
        self._token    = token or APIFY_TOKEN
        self._wait     = wait_sec
        self._throttle = throttle

    def run_actor(self, actor_id: str, payload: dict) -> list[dict]:
        """
        Ejecuta un actor de Apify y retorna los items crudos del dataset.
        No procesa, no parsea — retorna la lista tal como viene.

        Parámetros:
            actor_id: ID del actor (ej: "apify~instagram-scraper")
            payload:  input JSON del actor

        Retorna:
            lista de dicts crudos, o [] si hubo error
        """
        run_url = (
            f"https://api.apify.com/v2/acts/{actor_id}/runs"
            f"?token={self._token}&waitForFinish={self._wait}"
        )
        try:
            log.info("Apify: iniciando actor %s", actor_id)
            run_resp = requests.post(
                run_url, json=payload, timeout=self._wait + 30
            )
            run_resp.raise_for_status()

            dataset_id  = run_resp.json()["data"]["defaultDatasetId"]
            items_url   = (
                f"https://api.apify.com/v2/datasets/{dataset_id}/items"
                f"?token={self._token}"
            )
            items_resp  = requests.get(items_url, timeout=60)
            items_resp.raise_for_status()

            items = items_resp.json()
            log.info("Apify: actor %s devolvió %d items", actor_id, len(items))
            return items

        except requests.HTTPError as ex:
            log.error("Apify HTTP error [%s] %s: %s",
                      actor_id, ex.response.status_code, ex.response.text[:200])
            return []
        except requests.Timeout:
            log.error("Apify timeout [%s] después de %ds", actor_id, self._wait + 30)
            return []
        except Exception as ex:
            log.error("Apify error [%s]: %s", actor_id, ex)
            return []

    def throttle(self) -> None:
        """Pausa entre llamadas para no saturar Apify."""
        time.sleep(self._throttle)


# ─────────────────────────────────────────────────────────────
# PAYLOADS POR PLATAFORMA
# ─────────────────────────────────────────────────────────────
# Cada función construye su payload aquí.
# Si Apify cambia un parámetro, se cambia en un solo lugar.

def build_instagram_payload(urls: list[str]) -> dict:
    return {
        "directUrls":         urls,
        "resultsType":        "posts",
        "resultsLimit":       5,
        "onlyPostsNewerThan": "30 days",
        "addParentData":      False,
    }


def build_facebook_payload(urls: list[str]) -> dict:
    return {
        "startUrls":          [{"url": u} for u in urls],
        "resultsLimit":       5,
        "onlyPostsNewerThan": "20 days",
    }


def build_tiktok_payload(usernames: list[str]) -> dict:
    return {
        "profiles":              usernames,
        "resultsPerPage":        5,
        "profileScrapeSections": ["videos"],
        "profileSorting":        "latest",
        "scrapeRelatedVideos":   False,
        "shouldDownloadVideos":  False,
        "shouldDownloadCovers":  False,
        "commentsPerPost":       0,
    }


def build_linkedin_payload(company_urls: list[str]) -> dict:
    return {
        "companyUrls":  company_urls,
        "resultsLimit": 5,
        "sort":         "recent",
    }


def build_x_payload(handle: str) -> dict:
    return {
        "searchTerms":     [f"from:{handle}"],
        "maxTweets":       5,
        "sort":            "Latest",
        "withReplies":     False,
        "includeUserInfo": True,
    }


def build_web_payload(url: str) -> dict:
    return {
        "startUrls":            [{"url": url}],
        "maxCrawlPages":        3,
        "maxCrawlDepth":        1,
        "outputFormats":        ["text"],
        "removeCookieWarnings": True,
    }


# Actor IDs centralizados — si cambia el actor, se cambia aquí
ACTOR_INSTAGRAM = "apify~instagram-scraper"
ACTOR_FACEBOOK  = "apify~facebook-posts-scraper"
ACTOR_TIKTOK    = "clockworks~tiktok-scraper"
ACTOR_LINKEDIN  = "apimaestro~linkedin-company-posts"
ACTOR_X         = "xtdata~twitter-x-scraper"
ACTOR_WEB       = "apify~website-content-crawler"
