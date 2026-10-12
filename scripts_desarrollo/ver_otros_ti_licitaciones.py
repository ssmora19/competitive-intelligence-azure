# -*- coding: utf-8 -*-
"""Muestra las licitaciones que caen en 'Otros TI' para ver cuántas son TI real vs ruido."""
import os, sys, json, re, unicodedata

def _norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()

# consultar de nuevo SECOP para ver los objetos de Otros TI
import requests
from datetime import datetime, timezone, timedelta

def _auth():
    for r in ("local.settings.json",):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            kid, ksec = v.get("SOCRATA_API_KEY_ID"), v.get("SOCRATA_API_KEY_SECRET")
            return (kid, ksec) if kid and ksec else None
    return None

PROCESOS = "https://www.datos.gov.co/resource/p6dx-8zbt.json"
FAMILIAS_TI = {"8111","8112","8116","4320","4321","4322","4323"}
KEYWORDS_TAK = ["oracle","snowflake","red hat","azure","microsoft","computacion en la nube",
    "servicios en la nube","base de datos","data center","ciberseguridad","seguridad informatica",
    "licenciamiento de software","licencias de software","software","servidor","virtualizacion",
    "infraestructura tecnologica","desarrollo de software","sistema de informacion","business intelligence",
    "equipos de computo","computadores","data warehouse","endpoint"]
EXCLUIR = ["interventoria","construccion","estudios y disenos","obra civil","cerramiento",
    "operador logistico","dotacion","calzado","vestido","aseo","cafeteria","vigilancia",
    "transporte de","combustible","papeleria","arquitectura","urbanismo","pavimento"]

hoy = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00.000")
hasta = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%dT00:00:00.000")
params = {"$where": f"fecha_de_recepcion_de >= '{hoy}' AND fecha_de_recepcion_de <= '{hasta}'",
          "$limit": 1000, "$order": "fecha_de_recepcion_de ASC",
          "$select": "descripci_n_del_procedimiento,codigo_principal_de_categoria"}
r = requests.get(PROCESOS, params=params, auth=_auth(), timeout=60)
items = r.json()

# quedarse con las de "Otros TI": pasan filtro pero UNSPSC no está en familias conocidas
otros = []
for it in items:
    objeto = it.get("descripci_n_del_procedimiento","") or ""
    unspsc = it.get("codigo_principal_de_categoria","")
    obj = _norm(objeto)
    if any(x in obj for x in EXCLUIR): continue
    m = re.search(r"(\d{4})", str(unspsc or ""))
    es_familia = m and m.group(1) in FAMILIAS_TI
    if es_familia: continue  # esas ya están bien categorizadas
    if any(kw in obj for kw in KEYWORDS_TAK):
        otros.append((unspsc, objeto[:60]))

print(f"Licitaciones en 'Otros TI' (pasan por keyword, sin UNSPSC de TI): {len(otros)}\n")
for u, o in otros[:25]:
    print(f"  [{u:14}] {o}")