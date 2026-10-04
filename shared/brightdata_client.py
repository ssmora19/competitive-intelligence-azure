"""
shared/brightdata_client.py
===========================
Cliente centralizado para Bright Data (reemplaza a Apify para LinkedIn).

QUÉ HACE:
  Encapsula las llamadas a los "Scrapers" de Bright Data. El resto del codigo
  no hace requests directos: llama a este cliente, igual que antes con Apify.

PARA QUE SIRVE:
  Migracion de Apify -> Bright Data por costo (objetivo de TAK: ahorro).
  Bright Data da 5.000 creditos gratis/mes, suficientes para el volumen del
  proyecto (~380 registros/mes con corridas semanales).

DISENO — MISMA INTERFAZ QUE apify_client.py:
  Expone run_actor(actor_id, payload) -> list[dict], con LA MISMA FIRMA que el
  cliente de Apify, para que function_competidores.py y function_clientes.py
  NO tengan que reescribirse. Aqui 'actor_id' se interpreta como el dataset_id
  de Bright Data (gd_...), y el payload viene con la lista bajo "input".

FLUJO ASINCRONO (el correcto para estos scrapers de LinkedIn):
  Los scrapers de LinkedIn de Bright Data NO devuelven los datos al instante:
  scrapean en vivo y tardan. El flujo es de 3 pasos, que este cliente hace
  AUTOMATICAMENTE para que el resto del codigo no se entere:

    1. TRIGGER   POST /datasets/v3/trigger?dataset_id=...           -> snapshot_id
    2. POLL      GET  /datasets/v3/progress/{snapshot_id}           -> status: ready
    3. DOWNLOAD  GET  /datasets/v3/snapshot/{snapshot_id}?format=json -> datos

  (El endpoint sincrono /scrape devolvia un "still in progress" con el
  snapshot_id — por eso se usa este flujo async, que si espera y descarga.)

  Autenticacion: header  Authorization: Bearer <BRIGHTDATA_API_TOKEN>
"""

import os
import time
import logging

import requests

log = logging.getLogger("ci.brightdata")

BRIGHTDATA_API_TOKEN = os.getenv("BRIGHTDATA_API_TOKEN", "")

TRIGGER_URL  = "https://api.brightdata.com/datasets/v3/trigger"
PROGRESS_URL = "https://api.brightdata.com/datasets/v3/progress"
SNAPSHOT_URL = "https://api.brightdata.com/datasets/v3/snapshot"

POLL_INTERVAL_SEC = 12    # cada cuanto se consulta el progreso
POLL_MAX_WAIT_SEC = 480   # tope de espera total (8 min); deja margen ante los 10 min de Azure
HTTP_TIMEOUT      = 60
DEFAULT_THROTTLE  = 2


class BrightDataClient:
    """
    Cliente reutilizable para los Scrapers de Bright Data (modo asincrono).

    Uso (identico en forma al de Apify):
        client = BrightDataClient()
        items  = client.run_actor(DATASET_LINKEDIN_POSTS, payload,
                                  extra_params={"type": "discover_new",
                                                "discover_by": "company_url"})
    """

    def __init__(self, token: str = None, throttle: int = DEFAULT_THROTTLE):
        self._token    = token or BRIGHTDATA_API_TOKEN
        self._throttle = throttle
        self._headers  = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

    def run_actor(self, actor_id: str, payload: dict,
                  extra_params: dict | None = None) -> list[dict]:
        """
        Ejecuta un scraper de Bright Data (trigger + poll + download) y devuelve
        los registros crudos. No procesa ni parsea: retorna la lista tal cual.
        Retorna [] si hubo error o timeout.
        """
        if not self._token:
            log.error("BrightData: falta BRIGHTDATA_API_TOKEN en el entorno")
            return []

        snapshot_id = self._trigger(actor_id, payload, extra_params)
        if not snapshot_id:
            return []

        if not self._wait_until_ready(snapshot_id):
            log.error("BrightData: snapshot %s no quedo listo a tiempo", snapshot_id)
            return []

        return self._download(snapshot_id)

    def _trigger(self, dataset_id: str, payload: dict,
                 extra_params: dict | None) -> str | None:
        # /trigger espera la LISTA de inputs directamente como body
        body = payload.get("input", payload) if isinstance(payload, dict) else payload

        params = {"dataset_id": dataset_id, "include_errors": "true"}
        if extra_params:
            params.update(extra_params)
        if isinstance(payload, dict) and payload.get("limit_per_input") is not None:
            params["limit_per_input"] = payload["limit_per_input"]

        try:
            log.info("BrightData: trigger scraper %s", dataset_id)
            resp = requests.post(TRIGGER_URL, params=params, headers=self._headers,
                                 json=body, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            snapshot_id = data.get("snapshot_id")
            if not snapshot_id:
                log.error("BrightData: trigger sin snapshot_id: %s", str(data)[:200])
                return None
            log.info("BrightData: snapshot_id=%s", snapshot_id)
            return snapshot_id
        except requests.HTTPError as ex:
            code = ex.response.status_code if ex.response is not None else "?"
            txt  = ex.response.text[:200] if ex.response is not None else ""
            log.error("BrightData trigger HTTP %s [%s]: %s", code, dataset_id, txt)
            return None
        except Exception as ex:
            log.error("BrightData trigger error [%s]: %s", dataset_id, ex)
            return None

    def _wait_until_ready(self, snapshot_id: str) -> bool:
        url = f"{PROGRESS_URL}/{snapshot_id}"
        esperado = 0
        while esperado < POLL_MAX_WAIT_SEC:
            try:
                resp = requests.get(url, headers=self._headers, timeout=HTTP_TIMEOUT)
                resp.raise_for_status()
                status = resp.json().get("status", "")
                log.info("BrightData: snapshot %s status=%s", snapshot_id, status)
                if status == "ready":
                    return True
                if status in ("failed", "canceled"):
                    log.error("BrightData: snapshot %s termino en %s", snapshot_id, status)
                    return False
            except Exception as ex:
                log.warning("BrightData: progreso %s fallo: %s", snapshot_id, ex)
            time.sleep(POLL_INTERVAL_SEC)
            esperado += POLL_INTERVAL_SEC
        return False

    def _download(self, snapshot_id: str) -> list[dict]:
        url = f"{SNAPSHOT_URL}/{snapshot_id}"
        try:
            resp = requests.get(url, params={"format": "json"},
                                headers=self._headers, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("data") or data.get("results") or [data]
            else:
                items = []
            log.info("BrightData: snapshot %s devolvio %d items", snapshot_id, len(items))
            return items
        except requests.HTTPError as ex:
            code = ex.response.status_code if ex.response is not None else "?"
            txt  = ex.response.text[:200] if ex.response is not None else ""
            log.error("BrightData download HTTP %s [%s]: %s", code, snapshot_id, txt)
            return []
        except Exception as ex:
            log.error("BrightData download error [%s]: %s", snapshot_id, ex)
            return []

    def throttle(self) -> None:
        time.sleep(self._throttle)


# DATASET IDs — los "actores" de Bright Data
DATASET_LINKEDIN_POSTS   = "gd_lyy3tktm25m4avu764"
DATASET_LINKEDIN_JOBS    = "gd_lpfll7v5hcqtkxl6l"
DATASET_LINKEDIN_COMPANY = "gd_l1vikfnt1wgvvqz95w"

DISCOVER_POSTS = {"type": "discover_new", "discover_by": "company_url"}
DISCOVER_JOBS  = {"type": "discover_new", "discover_by": "keyword"}


def build_linkedin_posts_payload(company_urls: list[str]) -> dict:
    return {"input": [{"url": u} for u in company_urls], "limit_per_input": None}


def build_linkedin_jobs_payload(keywords: list[str], location: str = "Colombia",
                                country: str = "CO", time_range: str = "Past month") -> dict:
    return {
        "input": [
            {"keyword": kw, "location": location, "country": country,
             "time_range": time_range, "job_type": "", "experience_level": "",
             "remote": "", "company": "", "location_radius": ""}
            for kw in keywords
        ],
        "limit_per_input": None,
    }


def build_linkedin_company_payload(company_urls: list[str]) -> dict:
    return {"input": [{"url": u} for u in company_urls], "limit_per_input": None}