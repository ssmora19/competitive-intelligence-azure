# -*- coding: utf-8 -*-
"""
generar_licitaciones.py
=======================
Trae las LICITACIONES VIGENTES del mercado (procesos SECOP abiertos que aún no
cierran) filtradas por tecnologías del portafolio de TAK, y escribe la tabla
'goldLicitaciones' para la sección Alertas del tablero.

Responde al Tema 5 del cliente: "contratos por cerrarse que aplican para
ganarlos" — oportunidades abiertas donde TAK puede presentarse.

Filtra: fecha_de_recepcion_de >= hoy (vigente) Y <= hoy+N días (cierra pronto),
UNSPSC de TI o keyword de TAK en el objeto.

USO:  python generar_licitaciones.py            # simulacro
      python generar_licitaciones.py --escribir  # escribe la tabla
"""
from __future__ import annotations
import os, sys, json, re, time, hashlib, unicodedata
from datetime import datetime, timezone, timedelta

import requests

PROCESOS = "https://www.datos.gov.co/resource/p6dx-8zbt.json"
TABLA = "goldLicitaciones"
DIAS_FUTURO = 90   # licitaciones que cierran en los próximos 90 días

# Familias UNSPSC de TI (mismas que goldContratos)
FAMILIAS_TI = {"8111","8112","8116","4320","4322","4323"}
KEYWORDS_TAK = [
    "oracle","snowflake","red hat","websphere","azure","microsoft",
    "computacion en la nube","servicios en la nube","migracion a la nube",
    "base de datos","bases de datos","data center","centro de datos",
    "ciberseguridad","seguridad informatica","seguridad de la informacion",
    "licenciamiento de software","licencias de software","software",
    "servidor","servidores","virtualizacion","infraestructura tecnologica",
    "infraestructura de ti","desarrollo de software","sistema de informacion",
    "sistemas de informacion","business intelligence","migracion de datos",
    "data warehouse","endpoint","storage","almacenamiento empresarial","datacenter",
]

# Traducción UNSPSC -> categoría (misma lógica que contratos)
CATEGORIAS_6 = {
    "811122":"Consultoría TI","811115":"Servicios en la nube","811120":"Servicios en la nube",
    "811125":"Licenciamiento de software","811118":"Implementación de sistemas",
    "432315":"Soporte de software","432323":"Bases de datos","432325":"Software especializado",
    "801015":"Ciberseguridad","811123":"Mantenimiento de sistemas",
}
CATEGORIAS_4 = {
    "8111":"Servicios TI","8112":"Datos e internet",
    "8116":"Soporte de software","8310":"Telecom / portales","4323":"Software",
    "4321":"Equipos de cómputo","4322":"Redes","4320":"Electrónica",
}


def _norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"): return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No hay conexión"); sys.exit(1)


def _auth():
    kid = os.getenv("SOCRATA_API_KEY_ID"); ksec = os.getenv("SOCRATA_API_KEY_SECRET")
    if not (kid and ksec):
        for r in ("local.settings.json", os.path.join("..","local.settings.json")):
            if os.path.exists(r):
                v = json.load(open(r,encoding="utf-8")).get("Values",{})
                kid = kid or v.get("SOCRATA_API_KEY_ID"); ksec = ksec or v.get("SOCRATA_API_KEY_SECRET")
                break
    return (kid, ksec) if kid and ksec else None


def _categoria(unspsc, objeto=""):
    s = str(unspsc or "")
    m6 = re.search(r"(\d{6})", s)
    if m6 and m6.group(1) in CATEGORIAS_6: return CATEGORIAS_6[m6.group(1)]
    m4 = re.search(r"(\d{4})", s)
    if m4 and m4.group(1) in CATEGORIAS_4: return CATEGORIAS_4[m4.group(1)]
    # sin UNSPSC de TI: categorizar por el objeto (para las UNSPECIFIED)
    obj = _norm(objeto)
    # Hardware SOLO de infraestructura empresarial (servidores, storage, datacenter)
    # NO computadores/portátiles de usuario final (eso es commodity, no negocio TAK)
    if any(x in obj for x in ["servidor", "storage", "almacenamiento", "datacenter",
                               "data center", "san ", "nas ", "cabina de disco",
                               "infraestructura de computo", "blade", "rack"]):
        return "Infraestructura (hardware)"
    if any(x in obj for x in ["licencia", "office 365", "microsoft 365", "suscripcion",
                               "endpoint", "antivirus", "software"]):
        return "Software"
    if any(x in obj for x in ["ciberseguridad", "seguridad informatica", "penetracion",
                               "seguridad de la informacion", "seguridad a nivel"]):
        return "Ciberseguridad"
    if any(x in obj for x in ["soporte", "mantenimiento", "actualizacion"]):
        return "Soporte de software"
    if any(x in obj for x in ["nube", "cloud", "hosting", "infraestructura tecnolog",
                               "infraestructura de ti"]):
        return "Servicios en la nube"
    if any(x in obj for x in ["consultoria", "acompanamiento"]):
        return "Consultoría TI"
    return "Otros TI"


# Palabras que delatan obra civil / no-TI (aunque tengan keyword de TI)
EXCLUIR = [
    "interventoria", "construccion", "estudios y disenos", "estudios y diseno",
    "obra civil", "cerramiento", "adecuacion de infraestructura fisica",
    "operador logistico", "apoyo logistico", "dotacion", "calzado", "vestido", "aseo",
    "cafeteria", "vigilancia", "transporte de", "combustible", "papeleria",
    "mantenimiento de veh", "arquitectura", "urbanismo", "pavimento",
    "programa de seguros", "poliza", "equipo biomedico", "biomedico",
    "diseno metodologico", "apoyo a la gestion en el marco",
    # computadores/portátiles de usuario final (commodity, no es negocio de TAK)
    "computadores como estimulo", "estimulo educativo", "computadores portatiles para",
    "tablets", "tabletas", "computadores para estudiantes", "dotacion de computadores",
    "equipos de computo para estudiantes", "aulas", "kit escolar",
    # salud / óptica / no-TI que se colaba
    "lentes", "monturas", "optometria", "gafas", "medicad", "montura",
    "medicamento", "insumos medicos", "material medico", "odontolog",
    "certificados de navegacion", "navegacion segura",
    "suministro de alimentos", "raciones", "vestuario", "uniformes",
    "mobiliario", "muebles", "sillas", "escritorios",
]


def _es_ti(unspsc, objeto):
    """ESTRICTO: solo acepta licitaciones con UNSPSC claramente de TI.
    No acepta por keyword (evita ruido de prendas, lentes, etc.).
    Las familias de TI del catálogo UNSPSC:
      4321 = equipos de cómputo (servidores incluidos)
      4322 = equipos de red/telecom
      4323 = software
      8111 = servicios de TI
      8112 = servicios de datos/internet
      8116 = soporte de software
      4320 = componentes electrónicos de datos
    """
    # excluir explícitamente lo que no es TI (aunque tenga UNSPSC de TI mal asignado)
    obj = _norm(objeto)
    if any(x in obj for x in EXCLUIR):
        return False
    # SOLO por familia UNSPSC de TI a 6 dígitos (confiable)
    s = str(unspsc or "")
    m6 = re.search(r"(\d{6})", s)
    if not m6:
        return False   # sin UNSPSC de 6 dígitos -> no confiable -> fuera
    cod6 = m6.group(1)
    fam4 = cod6[:4]
    # Familias TI del portafolio de TAK (sin electrónica 4320, sin computadores usuario)
    # 4323=software, 8111=servicios TI, 8112=datos, 8116=soporte, 4322=redes
    if fam4 in {"4323","8111","8112","8116","4322"}:
        return True
    # 4321 (equipos de cómputo) SOLO servidores/datacenter, no computadores de usuario
    #   432115=servidores, 432116=servidores, 432119=almacenamiento, 432118=mainframe
    if cod6 in {"43211500","43211501","43211502","43211503","43211507","43211508",
                "43211900","43212100","43222600","43201800","43201500"}:
        # verificar que el objeto mencione servidor/infraestructura, no PC de usuario
        if any(x in obj for x in ["servidor","storage","almacenamiento","datacenter",
                                   "data center","infraestructura","san","nas","blade","rack"]):
            return True
        return False
    return False



def traer_licitaciones():
    """Consulta SECOP por licitaciones vigentes (recepción de ofertas futura)."""
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00.000")
    hasta = (datetime.now(timezone.utc) + timedelta(days=DIAS_FUTURO)).strftime("%Y-%m-%dT00:00:00.000")
    auth = _auth()
    # traer varias páginas para tener suficientes TI
    todas = []
    for offset in range(0, 3000, 1000):
        params = {
            "$where": f"fecha_de_recepcion_de >= '{hoy}' AND fecha_de_recepcion_de <= '{hasta}'",
            "$limit": 1000, "$offset": offset,
            "$order": "fecha_de_recepcion_de ASC",
            "$select": "id_del_proceso,referencia_del_proceso,entidad,departamento_entidad,"
                       "ciudad_entidad,descripci_n_del_procedimiento,codigo_principal_de_categoria,"
                       "modalidad_de_contratacion,precio_base,fecha_de_publicacion_del,"
                       "fecha_de_recepcion_de,estado_resumen,urlproceso",
        }
        ok = False
        for i in range(4):
            try:
                r = requests.get(PROCESOS, params=params, auth=auth, timeout=60)
                r.raise_for_status()
                items = r.json()
                todas.extend(items)
                ok = True
                break
            except Exception as ex:
                print(f"  offset {offset} intento {i+1}: {type(ex).__name__}"); time.sleep(4)
        if not ok or len(items) < 1000:
            break
        time.sleep(1)
    return todas


def main():
    escribir = "--escribir" in sys.argv
    print(f"Consultando licitaciones vigentes (cierran en los próximos {DIAS_FUTURO} días)...")
    crudas = traer_licitaciones()
    print(f"  Trajo {len(crudas)} licitaciones vigentes (todas las categorías)")

    filas = []
    for it in crudas:
        objeto = it.get("descripci_n_del_procedimiento","") or ""
        unspsc = it.get("codigo_principal_de_categoria","")
        if not _es_ti(unspsc, objeto):
            continue
        try:
            valor = float(it.get("precio_base") or 0)
        except (TypeError, ValueError):
            valor = 0
        url = (it.get("urlproceso") or {}).get("url","") if isinstance(it.get("urlproceso"), dict) else ""
        cierre = str(it.get("fecha_de_recepcion_de",""))[:10]
        rk = hashlib.sha256((it.get("id_del_proceso","") + url).encode("utf-8")).hexdigest()[:40]
        filas.append({
            "PartitionKey": "licitacion",
            "RowKey": rk,
            "objeto": objeto[:400],
            "entidad": it.get("entidad","?"),
            "ubicacion": " / ".join(x for x in [it.get("departamento_entidad"), it.get("ciudad_entidad")] if x),
            "categoria": _categoria(unspsc, objeto),
            "unspsc": unspsc or "",
            "modalidad": it.get("modalidad_de_contratacion",""),
            "valor": valor,
            "fecha_publicacion": str(it.get("fecha_de_publicacion_del",""))[:10],
            "fecha_cierre": cierre,
            "estado": it.get("estado_resumen",""),
            "url": url,
        })

    print(f"  Licitaciones de TI (portafolio TAK): {len(filas)}")
    # resumen por categoría
    from collections import Counter
    cats = Counter(f["categoria"] for f in filas)
    print("\n  Por categoría:")
    for c, n in cats.most_common():
        print(f"    {c:28} {n}")

    if filas:
        print("\n  Ejemplos (las que cierran primero):")
        for f in sorted(filas, key=lambda x: x["fecha_cierre"])[:5]:
            print(f"    cierra {f['fecha_cierre']} | {f['categoria'][:20]:20} | {f['objeto'][:40]}")

    if not escribir:
        print("\n[SIMULACRO] No escribí la tabla. Corre con --escribir para crearla.")
        return

    from azure.data.tables import TableServiceClient, UpdateMode
    tsc = TableServiceClient.from_connection_string(_conn())
    try:
        tsc.delete_table(TABLA); time.sleep(3)
    except Exception:
        pass
    dest = None
    for _ in range(12):
        try:
            dest = tsc.create_table_if_not_exists(TABLA); break
        except Exception as ex:
            if "BeingDeleted" in str(ex) or "Conflict" in str(ex): time.sleep(15)
            else: raise
    for f in filas:
        dest.upsert_entity(f, mode=UpdateMode.REPLACE)
    print(f"\nListo. Tabla '{TABLA}' con {len(filas)} licitaciones vigentes. Refresca Power BI.")


if __name__ == "__main__":
    main()