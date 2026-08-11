"""
shared/github_client.py

Cliente para GitHub API — tendencias tecnológicas.

Endpoints usados:
  - /search/repositories → repositorios trending por tecnología
  - /search/topics       → temas tecnológicos emergentes

Sin API key → 60 requests/hora (anónimo)
Con API key → 5,000 requests/hora (GITHUB_TOKEN en local.settings.json)

Relevancia para TAK:
  - Detectar tecnologías emergentes en el ecosistema Oracle, Snowflake, cloud
  - Ver qué proyectos open source están ganando tracción
  - Identificar tendencias antes de que lleguen al mercado colombiano
"""

import os
import time
import logging
from datetime import datetime, timezone, timedelta

import requests

log = logging.getLogger("ci.github")

GITHUB_BASE  = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Tecnologías relevantes para TAK a monitorear en GitHub
TOPICS_TAK = [
    "oracle", "snowflake", "red-hat", "openshift",
    "azure", "aws", "google-cloud", "kubernetes",
    "data-warehouse", "cloud-migration", "cybersecurity",
    "infrastructure-as-code", "devops", "data-lake",
]

DEFAULT_THROTTLE = 2


class GitHubClient:
    """
    Cliente para GitHub API.

    Uso:
        client = GitHubClient()
        repos  = client.get_trending_repos(topic="oracle", dias=30)
    """

    def __init__(self, token: str = None, throttle: int = DEFAULT_THROTTLE):
        self._token    = token or GITHUB_TOKEN
        self._throttle = throttle
        self._headers  = {
            "Accept":     "application/vnd.github.v3+json", #la versión de la API de GitHub que se va a usar
            "User-Agent": "TAK-InteligenciaCompetitiva/1.0", #el agente de usuario para identificar la aplicación
        }
        if self._token:
            self._headers["Authorization"] = f"Bearer {self._token}"

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """GET a la API de GitHub."""
        url = f"{GITHUB_BASE}{endpoint}"
        try:
            resp = requests.get(
                url, params=params,
                headers=self._headers, timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as ex:
            log.error("GitHub HTTP error [%s]: %s", endpoint, ex)
            return {}
        except Exception as ex:
            log.error("GitHub error [%s]: %s", endpoint, ex)
            return {}

    def get_trending_repos(
        self,
        topic:        str,
        dias:         int = 30,
        limite:       int = 10,
        min_stars:    int = 50,
    ) -> list[dict]:
        """
        Repositorios trending de un topic en los últimos N días.

        Retorna los items crudos de la API de GitHub.
        Incluye: nombre, descripción, stars, forks, lenguaje, URL.
        """
        fecha_desde = (
            datetime.now(timezone.utc) - timedelta(days=dias) #calcula la fecha desde la cual se van a buscar los repositorios, restando los días especificados a la fecha actual
        ).strftime("%Y-%m-%d")

        data = self._get("/search/repositories", params={
            "q":     f"topic:{topic} stars:>={min_stars} pushed:>={fecha_desde}", #busca repositorios con el topic especificado, con al menos min_stars estrellas y que hayan sido actualizados después de fecha_desde
            "sort":  "stars", #los resultados se ordenan por número de estrellas
            "order": "desc",
            "per_page": limite,
        })

        items = data.get("items", [])
        for item in items:
            item["_topic_buscado"] = topic  # metadato de ingestión
        log.info("GitHub: %d repos para topic=%s", len(items), topic)
        return items

    def get_trending_all_topics(
        self,
        dias:   int = 30,
        limite: int = 5,
    ) -> list[dict]:
        """
        Repositorios en tendencia para todos los topics relevantes a TAK.
        Retorna lista consolidada con metadato _topic_buscado.
        """
        todos = []
        for topic in TOPICS_TAK:
            repos = self.get_trending_repos(topic=topic, dias=dias, limite=limite)
            todos.extend(repos)
            time.sleep(self._throttle)
        return todos

    def throttle(self) -> None:
        time.sleep(self._throttle)
