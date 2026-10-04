# -*- coding: utf-8 -*-
"""
generar_contratos.py  (v3 — filtro por portafolio TAK + campos nuevos)
======================================================================
Extrae el detalle de contratos SECOP y los escribe en la tabla 'goldContratos':
  1) Contratos de COMPETIDORES: leídos desde silver-competidores.
  2) Contratos de TAK: consultados en vivo a SECOP (comparación "nosotros vs ellos").

NOVEDADES v3:
  - FILTRO POR PORTAFOLIO TAK: solo incluye contratos de TI (relevantes para TAK),
    por familia UNSPSC de TI O por keyword en el objeto. Quita el ruido
    (enfermería, papelería, personal, etc.).
  - CAMPOS NUEVOS: fecha_fin, fecha_firma, fecha_inicio, duracion, prorrogable,
    unspsc, y financiero (valor_pagado, valor_pendiente). Para líneas de tiempo,
    alertas de vencimiento y clasificación por tecnología.

FILTROS DE LIMPIEZA (iguales para todos):
  - Excluye monto >= $500.000 millones (errores de dato) y monto <= 0.
  - Excluye estados 'Cancelado' y 'Borrador'.
  - Excluye contratos NO relevantes para el portafolio de TAK.

USO:  python generar_contratos.py            # simulacro (cuenta, no escribe)
      python generar_contratos.py --escribir  # escribe la tabla goldContratos
"""
from __future__ import annotations
import os, sys, json, hashlib, re, unicodedata

try:
    import requests
except ImportError:
    print("Falta 'requests'. Instala: pip install requests"); sys.exit(1)

CONTENEDOR = "silver-competidores"
TABLA = "goldContratos"

TOPE_MONTO = 500_000_000_000
ESTADOS_EXCLUIDOS = {"cancelado", "borrador"}

# Competidores PRIORITARIOS que el cliente pidió monitorear (los únicos que van
# a goldContratos). Excluye los ~20 detectados por exploración (Indra, Deloitte,
# Capgemini, etc.) que NO son competencia directa de TAK.
COMPETIDORES_PRIORITARIOS = {
    "seti", "it crowd", "comware", "iteria", "bmind",
    "global technology services", "grow data",
}


def _es_prioritario(competidor: str) -> bool:
    return (competidor or "").strip().lower() in COMPETIDORES_PRIORITARIOS


TAK_NOMBRE_SECOP = "TECH AND KNOWLEDGE SAS"
SECOP_URL = "https://www.datos.gov.co/resource/jbjy-vk9h.json"

# ─────────────────────────────────────────────────────────────
# FILTRO POR PORTAFOLIO DE TAK
# ─────────────────────────────────────────────────────────────
# Familias UNSPSC de TI (los primeros 4 dígitos del código)
FAMILIAS_TI = {
    "8111",  # Servicios TI / desarrollo
    "8110",  # Servicios TI general
    "8112",  # Servicios de datos / internet
    "8116",  # Soporte y garantía de software
    "8310",  # Servicios de telecom/portales (WebSphere, etc.)
    "4320",  # Componentes electrónicos
    "4321",  # Hardware / computadores
    "4322",  # Redes / telecom
    "4323",  # Software
}

# Keywords ESTRICTAS de TI (solo términos claramente del portafolio TAK).
# Se quitaron las genéricas ("datos", "redes", "aplicaciones", "analitica",
# "respaldo", "almacenamiento") que cazaban ruido de contratos no-TI.
KEYWORDS_TAK = [
    "oracle", "snowflake", "red hat", "websphere", "azure", "microsoft azure",
    "amazon web services", "google cloud",
    "nube publica", "nube privada", "nube hibrida", "computacion en la nube",
    "migracion a la nube", "servicios en la nube",
    "base de datos", "bases de datos", "data warehouse", "data center",
    "centro de datos",
    "ciberseguridad", "seguridad informatica", "seguridad de la informacion",
    "licenciamiento de software", "licencias de software", "licenciamiento oracle",
    "soporte oracle", "software oracle",
    "servidor", "servidores", "virtualizacion", "infraestructura tecnologica",
    "infraestructura de ti", "infraestructura ti",
    "desarrollo de software", "fabrica de software", "sistema de informacion",
    "sistemas de informacion", "erp", "business intelligence", "inteligencia de negocios",
    "migracion de datos", "plataforma tecnologica",
]


# Traducción de UNSPSC -> categoría legible.
# Primero intenta a 6 dígitos (específico); si no, cae al de 4 dígitos (amplio).
CATEGORIAS_UNSPSC_6 = {
    "811122": "Consultoría TI", "811115": "Servicios en la nube",
    "811120": "Servicios en la nube", "811125": "Licenciamiento de software",
    "811118": "Implementación de sistemas", "811117": "Modernización de sistemas",
    "811121": "Software especializado", "811123": "Mantenimiento de sistemas",
    "811015": "Interventoría / auditoría TI", "801015": "Ciberseguridad",
    "801116": "Consultoría TI", "831015": "Consultoría TI",
    "432315": "Soporte de software", "432323": "Bases de datos",
    "432325": "Software especializado", "432322": "Software especializado",
    "432330": "Licenciamiento de software", "432327": "Soporte de software",
    "432332": "Licenciamiento de software", "432115": "Ciberseguridad",
    "432018": "Respaldo y recuperación", "432225": "Soporte de redes",
    "731521": "Mantenimiento de equipos",
}
CATEGORIAS_UNSPSC_4 = {
    "8111": "Servicios TI", "8110": "Servicios TI", "8112": "Datos e internet",
    "8116": "Soporte de software", "8310": "Telecom / portales",
    "4323": "Software", "4321": "Hardware", "4322": "Redes", "4320": "Electrónica",
}

# Tecnologías específicas que se detectan en el objeto del contrato.
# El orden importa: se devuelve la primera que aparezca.
TECNOLOGIAS_DETECTABLES = [
    ("Oracle", ["oracle"]),
    ("Red Hat", ["red hat", "redhat"]),
    ("SAP", ["sap "]),
    ("Microsoft", ["microsoft", "azure", "office 365", "windows server"]),
    ("VMware", ["vmware", "virtualizacion"]),
    ("AWS", ["aws", "amazon web"]),
    ("Google Cloud", ["google cloud", "gcp"]),
    ("Snowflake", ["snowflake"]),
    ("IBM", ["websphere", "ibm "]),
    ("Ciberseguridad", ["ciberseguridad", "seguridad informatica", "firewall", "antivirus"]),
    ("Nube", ["nube", "cloud", "iaas", "paas", "saas"]),
    ("Base de datos", ["base de datos", "bases de datos", "sql"]),
    ("Business Intelligence", ["business intelligence", "inteligencia de negocios", "power bi"]),
]


def _categoria_unspsc(unspsc):
    import re as _re
    s = str(unspsc or "")
    # 1) intentar a 6 dígitos (específico)
    m6 = _re.search(r"(\d{6})", s)
    if m6 and m6.group(1) in CATEGORIAS_UNSPSC_6:
        return CATEGORIAS_UNSPSC_6[m6.group(1)]
    # 2) caer a 4 dígitos (amplio)
    m4 = _re.search(r"(\d{4})", s)
    if m4 and m4.group(1) in CATEGORIAS_UNSPSC_4:
        return CATEGORIAS_UNSPSC_4[m4.group(1)]
    return "Otros TI"


def _detectar_tecnologia(objeto):
    obj = _norm(objeto)
    for nombre, patrones in TECNOLOGIAS_DETECTABLES:
        if any(p in obj for p in patrones):
            return nombre
    return "General"


def _norm(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower()


def es_relevante_tak(secop: dict, objeto_texto: str) -> bool:
    """True si el contrato es del portafolio de TAK. ESTRICTO: UNSPSC de TI, o
    keyword específica de TI en el objeto. Sin objeto ni UNSPSC de TI -> fuera."""
    # 1) por familia UNSPSC de TI (lo más confiable)
    unspsc = str(secop.get("unspsc") or "")
    m = re.search(r"(\d{4})", unspsc)
    if m and m.group(1) in FAMILIAS_TI:
        return True
    # 2) por keyword específica en el objeto (solo si el objeto tiene texto real)
    objeto = _norm(objeto_texto)
    if not objeto or objeto.strip() in ("no definido", "sin descripcion", ""):
        return False
    return any(kw in objeto for kw in KEYWORDS_TAK)


def _conn() -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json"),
              os.path.join("..", "..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING."); sys.exit(1)


def _rk(competidor: str, id_secop: str, url: str) -> str:
    base = f"{competidor}|{id_secop}|{url}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:40]


def _pasa_filtros(valor: float, estado: str) -> bool:
    if valor <= 0:
        return False
    if valor >= TOPE_MONTO:
        return False
    if (estado or "").strip().lower() in ESTADOS_EXCLUIDOS:
        return False
    return True


def _fila(competidor, entidad, objeto, valor, estado, tipo, ubicacion, id_secop, url, es_tak,
          unspsc="", fecha_firma="", fecha_inicio="", fecha_fin="", duracion="",
          prorrogable="", valor_pagado=0, valor_pendiente=0):
    return {
        "PartitionKey": ("TAK" if es_tak else "COMPETIDOR"),
        "RowKey": _rk(competidor, id_secop, url),
        "competidor": competidor,
        "es_tak": "Sí" if es_tak else "No",
        "entidad_contratante": entidad,
        "objeto": (objeto or "")[:500],
        "valor": float(valor or 0),
        "estado": estado,
        "tipo_contrato": tipo,
        "ubicacion": ubicacion,
        "id_secop": id_secop,
        "url": url,
        # --- campos nuevos ---
        "unspsc": unspsc or "",
        "fecha_firma": fecha_firma or "",
        "fecha_inicio": fecha_inicio or "",
        "fecha_fin": fecha_fin or "",
        "duracion": duracion or "",
        "prorrogable": prorrogable or "",
        "valor_pagado": float(valor_pagado or 0),
        "valor_pendiente": float(valor_pendiente or 0),
        "categoria": _categoria_unspsc(unspsc),
        "tecnologia": _detectar_tecnologia(objeto or ""),
    }


def leer_competidores() -> list:
    from azure.storage.blob import BlobServiceClient
    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(CONTENEDOR)
    filas = []
    excl_filtro = 0
    excl_norelevante = 0
    for blob in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        secop = d.get("secop")
        if not isinstance(secop, dict):
            continue
        # FILTRO: solo competidores prioritarios (excluye Indra y los exploratorios)
        if not _es_prioritario(d.get("entidad_declarada", "")):
            continue
        try:
            valor = float(secop.get("valor") or 0)
        except (TypeError, ValueError):
            valor = 0
        estado = secop.get("estado", "")
        if not _pasa_filtros(valor, estado):
            excl_filtro += 1
            continue
        # objeto: preferir el del bloque secop, si no el titulo
        objeto = secop.get("objeto") or d.get("titulo") or ""
        # FILTRO POR PORTAFOLIO TAK
        if not es_relevante_tak(secop, objeto):
            excl_norelevante += 1
            continue
        filas.append(_fila(
            d.get("entidad_declarada", "?"),
            secop.get("entidad_contratante", "?"),
            objeto,
            valor, estado,
            secop.get("tipo_contrato", ""),
            secop.get("ubicacion", ""),
            secop.get("id_secop", ""),
            d.get("url", ""),
            es_tak=False,
            unspsc=secop.get("unspsc", ""),
            fecha_firma=secop.get("fecha_firma", ""),
            fecha_inicio=secop.get("fecha_inicio", ""),
            fecha_fin=secop.get("fecha_fin", ""),
            duracion=secop.get("duracion", ""),
            prorrogable=secop.get("prorrogable", ""),
            valor_pagado=secop.get("valor_pagado", 0),
            valor_pendiente=secop.get("valor_pendiente_ejecucion", 0),
        ))
    print(f"  Competidores: {len(filas)} contratos de TI (excluidos {excl_filtro} por filtro monto/estado, {excl_norelevante} por no ser de TI)")
    return filas


def leer_tak() -> list:
    import time
    params = {
        "$where": f"proveedor_adjudicado like '%{TAK_NOMBRE_SECOP}%'",
        "$limit": 200,
        "$select": "referencia_del_contrato,nombre_entidad,objeto_del_contrato,"
                   "descripcion_del_proceso,codigo_de_categoria_principal,"
                   "valor_del_contrato,valor_pagado,valor_pendiente_de_ejecucion,"
                   "estado_contrato,tipo_de_contrato,proveedor_adjudicado,departamento,"
                   "fecha_de_firma,fecha_de_inicio_del_contrato,fecha_de_fin_del_contrato,"
                   "duraci_n_del_contrato,el_contrato_puede_ser_prorrogado,urlproceso",
    }
    # auth de Socrata (mismo token del secop_client)
    auth = _socrata_auth()
    items = None
    for intento in range(1, 7):
        try:
            print(f"  TAK: consultando SECOP (intento {intento}/6)...")
            r = requests.get(SECOP_URL, params=params, auth=auth, timeout=90)
            r.raise_for_status()
            items = r.json()
            break
        except Exception as ex:
            print(f"     no respondió ({type(ex).__name__}); reintento en 5s...")
            time.sleep(5)
    if items is None:
        print("  TAK: SECOP no respondió tras 6 intentos. Deja los competidores y reintenta TAK luego.")
        return []
    filas = []
    excl = 0
    excl_nr = 0
    for it in items:
        try:
            valor = float(it.get("valor_del_contrato") or 0)
        except (TypeError, ValueError):
            valor = 0
        estado = it.get("estado_contrato", "")
        if not _pasa_filtros(valor, estado):
            excl += 1
            continue
        objeto = it.get("objeto_del_contrato") or it.get("descripcion_del_proceso") or ""
        secop_fake = {"unspsc": it.get("codigo_de_categoria_principal", "")}
        if not es_relevante_tak(secop_fake, objeto):
            excl_nr += 1
            continue
        url = (it.get("urlproceso") or {}).get("url", "") if isinstance(it.get("urlproceso"), dict) else ""
        try:
            pagado = float(it.get("valor_pagado") or 0)
        except (TypeError, ValueError):
            pagado = 0
        try:
            pendiente = float(it.get("valor_pendiente_de_ejecucion") or 0)
        except (TypeError, ValueError):
            pendiente = 0
        filas.append(_fila(
            "TAK (Tech & Knowledge)",
            it.get("nombre_entidad", "?"),
            objeto,
            valor, estado,
            it.get("tipo_de_contrato", ""),
            it.get("departamento", ""),
            it.get("referencia_del_contrato", ""),
            url,
            es_tak=True,
            unspsc=it.get("codigo_de_categoria_principal", ""),
            fecha_firma=it.get("fecha_de_firma", ""),
            fecha_inicio=it.get("fecha_de_inicio_del_contrato", ""),
            fecha_fin=it.get("fecha_de_fin_del_contrato", ""),
            duracion=it.get("duraci_n_del_contrato", ""),
            prorrogable=it.get("el_contrato_puede_ser_prorrogado", ""),
            valor_pagado=pagado,
            valor_pendiente=pendiente,
        ))
    print(f"  TAK: {len(filas)} contratos de TI (excluidos {excl} por filtro, {excl_nr} por no ser de TI)")
    return filas


def _socrata_auth():
    """Lee el token de Socrata de local.settings.json (para no chocar con rate limit)."""
    kid = os.getenv("SOCRATA_API_KEY_ID")
    ksec = os.getenv("SOCRATA_API_KEY_SECRET")
    if not (kid and ksec):
        for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
            if os.path.exists(r):
                try:
                    v = json.load(open(r, encoding="utf-8")).get("Values", {})
                    kid = kid or v.get("SOCRATA_API_KEY_ID")
                    ksec = ksec or v.get("SOCRATA_API_KEY_SECRET")
                except Exception:
                    pass
                break
    return (kid, ksec) if kid and ksec else None


def main() -> None:
    escribir = "--escribir" in sys.argv

    print("Leyendo contratos de COMPETIDORES (Silver)...")
    filas = leer_competidores()
    print("\nConsultando contratos de TAK en SECOP...")
    filas += leer_tak()

    comp = [f for f in filas if f["es_tak"] == "No"]
    tak = [f for f in filas if f["es_tak"] == "Sí"]
    suma_comp = sum(f["valor"] for f in comp)
    suma_tak = sum(f["valor"] for f in tak)

    print("\n" + "=" * 60)
    print("RESUMEN (solo contratos de TI / portafolio TAK)")
    print("=" * 60)
    print(f"  Contratos COMPETIDORES: {len(comp)}  -> ${suma_comp:,.0f}")
    print(f"  Contratos TAK:          {len(tak)}  -> ${suma_tak:,.0f}")
    if suma_comp:
        print(f"  TAK captura el {100*suma_tak/(suma_comp+suma_tak):.1f}% del total")

    if not escribir:
        print("\n[SIMULACRO] No escribí la tabla. Corre con --escribir para crearla.")
        return

    from azure.data.tables import TableServiceClient, UpdateMode
    import time as _t
    tsc = TableServiceClient.from_connection_string(_conn())
    # borrar la tabla vieja para no acumular contratos de corridas anteriores
    try:
        tsc.delete_table(TABLA)
        print(f"  (tabla '{TABLA}' vieja borrada, recreando...)")
    except Exception:
        pass
    tabla = None
    for _i in range(12):
        try:
            tabla = tsc.create_table_if_not_exists(TABLA)
            break
        except Exception as ex:
            if "BeingDeleted" in str(ex) or "Conflict" in str(ex):
                print(f"     Azure libera la tabla; espero 15s ({_i+1}/12)...")
                _t.sleep(15)
            else:
                raise
    if tabla is None:
        print("La tabla sigue en borrado. Espera 1 min y reintenta."); return
    print(f"\nEscribiendo {len(filas)} contratos en '{TABLA}'...")
    for f in filas:
        tabla.upsert_entity(f, mode=UpdateMode.REPLACE)
    print(f"Listo. Tabla '{TABLA}' con {len(comp)} de competidores + {len(tak)} de TAK. Refresca Power BI.")


if __name__ == "__main__":
    main()