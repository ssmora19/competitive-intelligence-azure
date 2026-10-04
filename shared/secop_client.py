"""
shared/secop_client.py
======================
Cliente para la API de SECOP II — Datos Abiertos Colombia.

API: Socrata SODA (datos.gov.co)
Datasets:
  jbjy-vk9h → SECOP II Contratos Electrónicos
  p6dx-8zbt → SECOP II Procesos de Contratación

Sin API key — gratuita y pública. Filtros SoQL: $where, $limit, $offset, $order, $select

Relevancia para TAK (Tech & Knowledge):
  Portafolio: Oracle, Snowflake, IBM, Dell, Microsoft, Red Hat
  Servicios: implementación, migración, nube, infraestructura, ciberseguridad
  Mercado: 70% sector público, basado en Bogotá

─────────────────────────────────────────────────────────────
v3 — Aprovecha SECOP al 100%. Trae TODO lo necesario para el feedback del cliente:
  FECHAS:   fecha_de_firma, fecha_de_inicio, fecha_de_FIN, duración, prorrogable
            -> líneas de tiempo, alertas de vencimiento, renovaciones (Tema 1 y 5)
  QUÉ SE COMPRÓ: objeto_del_contrato + codigo_de_categoria_principal (UNSPSC)
            -> clasificar por servicio aunque el objeto esté vacío (Tema 2 y 4)
  FINANCIERO: valor, valor_pagado, valor_pendiente_de_ejecucion, valor_facturado
            -> estado real de ejecución del contrato
  ESTADO:   estado_contrato, liquidación
Todas las funciones de contratos usan el MISMO set completo (CAMPOS_CONTRATO).
─────────────────────────────────────────────────────────────
"""

import time
import logging
from typing import Optional

import requests

log = logging.getLogger("ci.secop")

SECOP_BASE        = "https://www.datos.gov.co/resource"
DATASET_CONTRATOS = "jbjy-vk9h"  # SECOP II — Contratos electrónicos
DATASET_PROCESOS  = "p6dx-8zbt"  # SECOP II — Procesos de contratación

# ─────────────────────────────────────────────────────────────
# CAMPOS COMPLETOS DE CONTRATO — un solo lugar para todas las consultas.
# Verificados como existentes en la API (jbjy-vk9h).
# ─────────────────────────────────────────────────────────────
CAMPOS_CONTRATO = (
    # identificación
    "referencia_del_contrato,id_contrato,proceso_de_compra,"
    "estado_contrato,"
    # entidad y proveedor
    "nombre_entidad,nit_entidad,departamento,ciudad,"
    "proveedor_adjudicado,"
    # qué se compró
    "descripcion_del_proceso,objeto_del_contrato,"
    "codigo_de_categoria_principal,"          # UNSPSC — categoría de lo comprado
    "tipo_de_contrato,modalidad_de_contratacion,"
    # FECHAS y duración
    "fecha_de_firma,fecha_de_inicio_del_contrato,"
    "fecha_de_fin_del_contrato,duraci_n_del_contrato,"
    "el_contrato_puede_ser_prorrogado,dias_adicionados,"
    "liquidaci_n,"
    # FINANCIERO
    "valor_del_contrato,valor_pagado,valor_facturado,"
    "valor_pendiente_de_ejecucion,valor_pendiente_de_pago,"
    "valor_amortizado,"
    # otros útiles
    "sector,rama,orden,origen_de_los_recursos,"
    "urlproceso"
)

# Campos completos para PROCESOS (licitaciones abiertas — oportunidades futuras)
CAMPOS_PROCESO = (
    # identificación
    "id_del_proceso,referencia_del_proceso,nombre_del_procedimiento,"
    # estado y si ya tiene ganador (CLAVE para licitaciones abiertas)
    "adjudicado,estado_del_procedimiento,estado_de_apertura_del_proceso,"
    "estado_resumen,fase,"
    # entidad compradora
    "entidad,nit_entidad,departamento_entidad,ciudad_entidad,"
    # qué se pide + clasificación
    "descripci_n_del_procedimiento,codigo_principal_de_categoria,"
    "tipo_de_contrato,modalidad_de_contratacion,"
    # presupuesto
    "precio_base,valor_total_adjudicacion,"
    # fechas y duración
    "fecha_de_publicacion_del,fecha_de_ultima_publicaci,"
    "duracion,unidad_de_duracion,"
    # competencia (cuántos están presentando)
    "proveedores_que_manifestaron,respuestas_al_procedimiento,"
    "nombre_del_proveedor,nit_del_proveedor_adjudicado,"
    "urlproceso"
)

# ─────────────────────────────────────────────────────────────
# KEYWORDS — específicas al portafolio de TAK
# ─────────────────────────────────────────────────────────────
KEYWORDS_TECNOLOGIAS = [
    "oracle", "snowflake", "red hat", "redhat",
    "azure", "aws", "google cloud", "nube publica",
    "nube privada", "hibrida",
]
KEYWORDS_SERVICIOS = [
    "implementacion de oracle", "migracion de base de datos",
    "migracion a la nube", "infraestructura ti",
    "alta disponibilidad", "ciberseguridad",
    "licenciamiento oracle", "licencias de software",
    "soporte oracle", "base de datos",
]
KEYWORDS_GENERALES = [
    "plataforma de datos", "data warehouse",
    "almacenamiento de datos", "ERP oracle",
    "transformacion digital ti",
]
KEYWORDS_TAK = KEYWORDS_TECNOLOGIAS + KEYWORDS_SERVICIOS + KEYWORDS_GENERALES

# Estados de contrato SIN datos útiles (objeto/fechas vacíos). Se excluyen en
# las consultas de contratos para traer solo contratos completos y presentables.
ESTADOS_EXCLUIDOS = ["Borrador", "Cancelado"]

def _filtro_estados_ok() -> str:
    """Cláusula SoQL que excluye los estados sin datos útiles."""
    return " AND ".join([f"estado_contrato != '{e}'" for e in ESTADOS_EXCLUIDOS])

DEFAULT_LIMIT    = 30
DEFAULT_THROTTLE = 1


class SecopClient:
    """Cliente para consultar SECOP II vía API de Datos Abiertos Colombia."""
    # ─────────────────────────────────────────────────────────
    # LICITACIONES VIGENTES (cierran pronto — oportunidades reales para TAK)
    # Filtra por fecha_de_recepcion_de futura (aún se pueden ganar), a
    # diferencia de get_procesos_activos_tak que traía procesos viejos.
    # ─────────────────────────────────────────────────────────
    def get_licitaciones_vigentes(self, dias_futuro: int = 90, limite: int = 1000, offset: int = 0) -> list[dict]:
        from datetime import datetime, timezone, timedelta
        hoy = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00.000")
        hasta = (datetime.now(timezone.utc) + timedelta(days=dias_futuro)).strftime("%Y-%m-%dT00:00:00.000")
        where = (f"fecha_de_recepcion_de >= '{hoy}' "
                 f"AND fecha_de_recepcion_de <= '{hasta}'")
        params = {
            "$where": where, "$limit": limite, "$offset": offset,
            "$order": "fecha_de_recepcion_de ASC",
            "$select": (
                "id_del_proceso,referencia_del_proceso,entidad,"
                "departamento_entidad,ciudad_entidad,"
                "descripci_n_del_procedimiento,codigo_principal_de_categoria,"
                "modalidad_de_contratacion,precio_base,"
                "fecha_de_publicacion_del,fecha_de_recepcion_de,"
                "estado_resumen,urlproceso"
            ),
        }
        return self._get(DATASET_PROCESOS, params)
    
    def __init__(self, throttle: int = DEFAULT_THROTTLE):
        self._throttle = throttle
        self._headers  = {
            "Accept":     "application/json",
            "User-Agent": "TAK-InteligenciaCompetitiva/1.0",
        }
        # Credenciales de Socrata (API Key ID + Secret) para subir el límite de
        # peticiones. Se leen de env o local.settings.json. Si no hay, funciona
        # igual pero con rate-limiting más agresivo (~60 req/hora).
        self._auth = self._cargar_auth()

    @staticmethod
    def _cargar_auth():
        import os, json
        kid = os.getenv("SOCRATA_API_KEY_ID")
        ksec = os.getenv("SOCRATA_API_KEY_SECRET")
        if not (kid and ksec):
            for ruta in ("local.settings.json", os.path.join("..", "local.settings.json"),
                         os.path.join("..", "..", "local.settings.json")):
                if os.path.exists(ruta):
                    try:
                        with open(ruta, encoding="utf-8") as f:
                            vals = json.load(f).get("Values", {})
                        kid = kid or vals.get("SOCRATA_API_KEY_ID")
                        ksec = ksec or vals.get("SOCRATA_API_KEY_SECRET")
                    except Exception:
                        pass
                    if kid and ksec:
                        break
        if kid and ksec:
            log.info("SECOP: usando credenciales Socrata (límite ampliado)")
            return (kid, ksec)
        log.warning("SECOP: sin credenciales Socrata (rate-limiting agresivo ~60/h)")
        return None

    def _get(self, dataset: str, params: dict) -> list[dict]:
        """GET a la API. timeout 25s, 1 reintento, [] ante cualquier fallo."""
        url = f"{SECOP_BASE}/{dataset}.json"
        intentos = 4
        for intento in range(1, intentos + 1):
            try:
                log.info("SECOP: consultando %s (intento %d)", dataset, intento)
                resp = requests.get(url, params=params, headers=self._headers,
                                    auth=self._auth, timeout=60)
                resp.raise_for_status()
                items = resp.json()
                log.info("SECOP: %d registros de %s", len(items), dataset)
                return items
            except (requests.Timeout, requests.ConnectionError) as ex:
                log.warning("SECOP timeout/conexion [%s] intento %d: %s", dataset, intento, ex)
                if intento < intentos:
                    time.sleep(2)
                    continue
                log.error("SECOP sin respuesta [%s] tras %d intentos", dataset, intentos)
                return []
            except requests.HTTPError as ex:
                log.error("SECOP HTTP error [%s]: %s", dataset, ex)
                return []
            except Exception as ex:
                log.error("SECOP error [%s]: %s", dataset, ex)
                return []
        return []

    def _build_keyword_filter(self, campo: str, keywords: list[str], max_kw: int = 10) -> str:
        return " OR ".join([f"{campo} like '%{kw}%'" for kw in keywords[:max_kw]])

    # ─────────────────────────────────────────────────────────
    # CONTRATOS DE TAK (los que TAK ya ganó)
    # ─────────────────────────────────────────────────────────
    def get_contratos_tak(self, dias_recientes: int = 30, limite: int = DEFAULT_LIMIT, solo_bogota: bool = False) -> list[dict]:
        from datetime import datetime, timezone, timedelta
        fecha_desde = (datetime.now(timezone.utc) - timedelta(days=dias_recientes)).strftime("%Y-%m-%dT00:00:00.000")
        keyword_filter = self._build_keyword_filter("descripcion_del_proceso", KEYWORDS_TECNOLOGIAS + KEYWORDS_SERVICIOS, max_kw=10)
        where = f"({keyword_filter}) AND fecha_de_firma >= '{fecha_desde}'"
        if solo_bogota:
            where += " AND (departamento like '%BOGOT%' OR departamento like '%CUNDINAMARCA%')"
        where += f" AND {_filtro_estados_ok()}"
        params = {"$where": where, "$limit": limite, "$order": "fecha_de_firma DESC", "$select": CAMPOS_CONTRATO}
        return self._get(DATASET_CONTRATOS, params)

    # ─────────────────────────────────────────────────────────
    # PROCESOS / LICITACIONES ABIERTAS (oportunidades futuras)
    # ─────────────────────────────────────────────────────────
    def get_procesos_activos_tak(self, limite: int = DEFAULT_LIMIT, solo_bogota: bool = False) -> list[dict]:
        keyword_filter = self._build_keyword_filter("descripci_n_del_procedimiento", KEYWORDS_TECNOLOGIAS + KEYWORDS_SERVICIOS, max_kw=10)
        where = f"({keyword_filter}) AND estado_de_apertura_del_proceso='Abierto'"
        if solo_bogota:
            where += " AND (departamento_entidad like '%BOGOT%' OR departamento_entidad like '%CUNDINAMARCA%')"
        params = {"$where": where, "$limit": limite, "$order": "fecha_de_publicacion_del DESC", "$select": CAMPOS_PROCESO}
        return self._get(DATASET_PROCESOS, params)

    # ─────────────────────────────────────────────────────────
    # CONTRATOS DE UN COMPETIDOR  (con fechas completas + UNSPSC + financiero)
    # ─────────────────────────────────────────────────────────
    def get_contratos_competidor(self, nombre_empresa: str, limite: int = 50, secop_nombres: list = None) -> list[dict]:
        secop_nombres = secop_nombres or []
        if secop_nombres:
            clausulas = []
            for nombre in secop_nombres:
                seguro = nombre.strip().replace("'", "''")
                clausulas.append(f"proveedor_adjudicado like '%{seguro}%'")
            where = " OR ".join(clausulas)
        else:
            palabras_genericas = {"tech", "the", "grupo", "group", "colombia",
                                  "global", "corporation", "s.a.s", "sas", "ltda"}
            palabras = [p for p in nombre_empresa.split() if p.lower() not in palabras_genericas]
            termino = palabras[0] if palabras else nombre_empresa.split()[0]
            where = f"proveedor_adjudicado like '%{termino}%'"
        where = f"({where}) AND {_filtro_estados_ok()}"
        params = {"$where": where, "$limit": limite, "$order": "fecha_de_firma DESC", "$select": CAMPOS_CONTRATO}
        items = self._get(DATASET_CONTRATOS, params)
        for item in items:
            item["_competidor_buscado"] = nombre_empresa
        return items

    # ─────────────────────────────────────────────────────────
    # PROCESOS por entidad compradora (clientes públicos)
    # ─────────────────────────────────────────────────────────
    def get_procesos_por_entidad(self, nombre_entidad: str, limite: int = 30) -> list[dict]:
        if not nombre_entidad or not nombre_entidad.strip():
            return []
        seguro = nombre_entidad.strip().replace("'", "''")
        params = {"$where": f"entidad like '%{seguro}%'", "$limit": limite, "$order": "fecha_de_publicacion_del DESC", "$select": CAMPOS_PROCESO}
        items = self._get(DATASET_PROCESOS, params)
        for item in items:
            item["_entidad_buscada"] = nombre_entidad
        return items

    # ─────────────────────────────────────────────────────────
    # CONTRATOS por entidad compradora (clientes públicos)
    # ─────────────────────────────────────────────────────────
    def get_contratos_por_entidad(self, nombre_entidad: str, limite: int = 30) -> list[dict]:
        if not nombre_entidad or not nombre_entidad.strip():
            return []
        seguro = nombre_entidad.strip().replace("'", "''")
        where = f"nombre_entidad like '%{seguro}%' AND {_filtro_estados_ok()}"
        params = {"$where": where, "$limit": limite, "$order": "fecha_de_firma DESC", "$select": CAMPOS_CONTRATO}
        items = self._get(DATASET_CONTRATOS, params)
        for item in items:
            item["_entidad_buscada"] = nombre_entidad
        return items

    # ─────────────────────────────────────────────────────────
    # Contratos por SERVICIO/TECNOLOGÍA (qué compra el mercado — Tema 4)
    # Se puede llamar con tecnologías que TAK NO tiene, para detectar tendencias.
    # ─────────────────────────────────────────────────────────
    def get_contratos_por_keyword(self, keyword: str, limite: int = 50, dias_recientes: int = 365) -> list[dict]:
        if not keyword or not keyword.strip():
            return []
        seguro = keyword.strip().replace("'", "''")
        from datetime import datetime, timezone, timedelta
        fecha_desde = (datetime.now(timezone.utc) - timedelta(days=dias_recientes)).strftime("%Y-%m-%dT00:00:00.000")
        where = (f"(objeto_del_contrato like '%{seguro}%' "
                 f"OR descripcion_del_proceso like '%{seguro}%') "
                 f"AND fecha_de_firma >= '{fecha_desde}'")
        where = f"({where}) AND {_filtro_estados_ok()}"
        params = {"$where": where, "$limit": limite, "$order": "fecha_de_firma DESC", "$select": CAMPOS_CONTRATO}
        items = self._get(DATASET_CONTRATOS, params)
        for item in items:
            item["_keyword_buscado"] = keyword
        return items

    def throttle(self) -> None:
        time.sleep(self._throttle)