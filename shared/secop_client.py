"""
shared/secop_client.py
======================
Cliente para la API de SECOP II — Datos Abiertos Colombia.

API: Socrata SODA (datos.gov.co)
URL base: https://www.datos.gov.co/resource/

Datasets principales:
  jbjy-vk9h → SECOP II Contratos Electrónicos
  p6dx-8zbt → SECOP II Procesos de Contratación

Sin API key — completamente gratuita y pública.
Soporta filtros SoQL: $where, $limit, $offset, $order, $select

Relevancia para TAK (Tech & Knowledge):
  Portafolio: Oracle, Snowflake, IBM, Dell, Microsoft, Red Hat
  Servicios: implementación, migración, nube, infraestructura, ciberseguridad
  Mercado: 70% sector público, basado en Bogotá
"""

import time
import logging
from typing import Optional

import requests

log = logging.getLogger("ci.secop")

# Endpoints de datos abiertos
SECOP_BASE        = "https://www.datos.gov.co/resource"
DATASET_CONTRATOS = "jbjy-vk9h"  # SECOP II — Contratos electrónicos
DATASET_PROCESOS  = "p6dx-8zbt"  # SECOP II — Procesos de contratación

# ─────────────────────────────────────────────────────────────
# KEYWORDS — específicas al portafolio de TAK
# Ordenadas de más a menos específicas
# ─────────────────────────────────────────────────────────────

# Tecnologías exactas que TAK vende
KEYWORDS_TECNOLOGIAS = [
    "oracle", "snowflake", "red hat", "redhat",
    "azure", "aws", "google cloud", "nube publica",
    "nube privada", "hibrida",
]

# Servicios específicos de TAK
KEYWORDS_SERVICIOS = [
    "implementacion de oracle", "migracion de base de datos",
    "migracion a la nube", "infraestructura ti",
    "alta disponibilidad", "ciberseguridad",
    "licenciamiento oracle", "licencias de software",
    "soporte oracle", "base de datos",
]

# Categorías TI precisas
KEYWORDS_GENERALES = [
    "plataforma de datos", "data warehouse",
    "almacenamiento de datos", "ERP oracle",
    "transformacion digital ti",
]

# Todas combinadas — se usan máximo 10 en cada query
KEYWORDS_TAK = (
    KEYWORDS_TECNOLOGIAS +
    KEYWORDS_SERVICIOS +
    KEYWORDS_GENERALES
)

DEFAULT_LIMIT    = 100
DEFAULT_THROTTLE = 1


class SecopClient:
    """
    Cliente para consultar SECOP II vía API de Datos Abiertos Colombia.

    Uso:
        client    = SecopClient()
        contratos = client.get_contratos_tak(dias_recientes=30)
        procesos  = client.get_procesos_activos_tak()
    """

    def __init__(self, throttle: int = DEFAULT_THROTTLE):
        self._throttle = throttle
        self._headers  = {
            "Accept":     "application/json",
            "User-Agent": "TAK-InteligenciaCompetitiva/1.0",
        }

    def _get(self, dataset: str, params: dict) -> list[dict]:
        """GET a la API de Datos Abiertos — sin API key."""
        url = f"{SECOP_BASE}/{dataset}.json"
        try:
            log.info("SECOP: consultando %s", dataset)
            resp = requests.get(
                url, params=params,
                headers=self._headers, timeout=60,
            )
            resp.raise_for_status()
            items = resp.json()
            log.info("SECOP: %d registros de %s", len(items), dataset)
            return items
        except requests.HTTPError as ex:
            log.error("SECOP HTTP error [%s]: %s", dataset, ex)
            return []
        except Exception as ex:
            log.error("SECOP error [%s]: %s", dataset, ex)
            return []

    def _build_keyword_filter(
        self,
        campo:    str,
        keywords: list[str],
        max_kw:   int = 10,
    ) -> str:
        """Construye filtro OR con keywords — máximo max_kw para no saturar la query."""
        return " OR ".join([
            f"{campo} like '%{kw}%'"
            for kw in keywords[:max_kw]
        ])

    def get_contratos_tak(
        self,
        dias_recientes: int = 30,
        limite:         int = DEFAULT_LIMIT,
        solo_bogota:    bool = False,
    ) -> list[dict]:
        """
        Contratos TI adjudicados relevantes para TAK — últimos N días.

        Filtra por tecnologías y servicios del portafolio de TAK:
        Oracle, Snowflake, nube, implementación, migración, etc.

        solo_bogota: si True, filtra solo contratos de Bogotá/Cundinamarca.
        """
        from datetime import datetime, timezone, timedelta
        fecha_desde = (
            datetime.now(timezone.utc) - timedelta(days=dias_recientes)
        ).strftime("%Y-%m-%dT00:00:00.000")

        # Combinar tecnologías + servicios — las más específicas primero
        keywords_ordenadas = KEYWORDS_TECNOLOGIAS + KEYWORDS_SERVICIOS
        keyword_filter = self._build_keyword_filter(
            "descripcion_del_proceso", keywords_ordenadas, max_kw=10
        )

        where = f"({keyword_filter}) AND fecha_de_firma >= '{fecha_desde}'"
        if solo_bogota:
            where += " AND (departamento like '%BOGOT%' OR departamento like '%CUNDINAMARCA%')"

        params = {
            "$where":  where,
            "$limit":  limite,
            "$order":  "fecha_de_firma DESC",
            "$select": (
                "referencia_del_contrato,estado_contrato,"
                "nombre_entidad,departamento,ciudad,"
                "descripcion_del_proceso,objeto_del_contrato,"
                "tipo_de_contrato,valor_del_contrato,"
                "fecha_de_firma,fecha_de_inicio_del_contrato,"
                "fecha_de_fin_del_contrato,proveedor_adjudicado,"
                "nit_entidad,urlproceso"
            ),
        }
        return self._get(DATASET_CONTRATOS, params)

    def get_procesos_activos_tak(
        self,
        limite:      int  = DEFAULT_LIMIT,
        solo_bogota: bool = False,
    ) -> list[dict]:
        """
        Licitaciones TI abiertas ahora — oportunidades para TAK.

        Estas son procesos en estado 'Abierto' donde TAK podría presentar propuesta.
        TAK solo participa en el 3-5% de estas — aquí está el 95% que está perdiendo.
        """
        keywords_ordenadas = KEYWORDS_TECNOLOGIAS + KEYWORDS_SERVICIOS
        keyword_filter = self._build_keyword_filter(
            "descripci_n_del_procedimiento", keywords_ordenadas, max_kw=10
        )

        where = f"({keyword_filter}) AND estado_de_apertura_del_proceso='Abierto'"
        if solo_bogota:
            where += " AND (departamento_entidad like '%BOGOT%' OR departamento_entidad like '%CUNDINAMARCA%')"

        params = {
            "$where":  where,
            "$limit":  limite,
            "$order":  "fecha_de_publicacion_del DESC",
            "$select": (
                "id_del_proceso,estado_del_procedimiento,"
                "estado_de_apertura_del_proceso,estado_resumen,"
                "entidad,departamento_entidad,ciudad_entidad,"
                "descripci_n_del_procedimiento,tipo_de_contrato,"
                "modalidad_de_contratacion,precio_base,"
                "fecha_de_publicacion_del,fecha_de_ultima_publicaci,"
                "urlproceso"
            ),
        }
        return self._get(DATASET_PROCESOS, params)

    def get_contratos_competidor(
        self,
        nombre_empresa: str,
        limite:         int = 50,
    ) -> list[dict]:
        """
        Contratos públicos de un competidor específico en SECOP II.
        Útil para ver qué está ganando la competencia en el sector público.
        """
        # Usar primera palabra significativa del nombre
        palabras_genericas = {"tech", "the", "grupo", "group", "colombia",
                              "global", "corporation", "s.a.s", "sas", "ltda"}
        palabras = [p for p in nombre_empresa.split()
                    if p.lower() not in palabras_genericas]
        termino = palabras[0] if palabras else nombre_empresa.split()[0]

        params = {
            "$where":  f"proveedor_adjudicado like '%{termino}%'",
            "$limit":  limite,
            "$order":  "fecha_de_firma DESC",
            "$select": (
                "referencia_del_contrato,estado_contrato,"
                "nombre_entidad,departamento,"
                "descripcion_del_proceso,objeto_del_contrato,"
                "tipo_de_contrato,valor_del_contrato,"
                "fecha_de_firma,proveedor_adjudicado,urlproceso"
            ),
        }
        items = self._get(DATASET_CONTRATOS, params)
        for item in items:
            item["_competidor_buscado"] = nombre_empresa
        return items

    def throttle(self) -> None:
        time.sleep(self._throttle)
