# -*- coding: utf-8 -*-
"""Verifica si podemos desglosar qué servicios pide cada entidad activa en SECOP."""
import os, sys, json, re, unicodedata
from collections import defaultdict, Counter

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
    sys.exit("No hay conexión")

# categorías UNSPSC 4 dígitos -> nombre
CAT4 = {"8111":"Servicios TI","8112":"Datos/internet","8116":"Soporte software",
        "4323":"Software","4321":"Equipos cómputo","4322":"Redes","8010":"Consultoría"}

from azure.storage.blob import BlobServiceClient
bs = BlobServiceClient.from_connection_string(_conn())

# entidades top que queremos desglosar
ENTIDADES = ["Ministerio de Transporte", "Loteria de Bogota", "DANE",
             "Universidad Militar", "POSITIVA", "Defensoría del Pueblo"]

CONTENEDORES = ["silver-clientes", "silver-clientes-competencia"]
print("Verificando si los procesos tienen categoría para desglosar...\n")

por_entidad = defaultdict(Counter)
con_unspsc = 0
sin_unspsc = 0
for cont in CONTENEDORES:
    try:
        cli = bs.get_container_client(cont)
        blobs = list(cli.list_blobs())
    except Exception:
        continue
    for b in blobs:
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        fuente = d.get("fuente_original","")
        if "proceso" not in fuente and "contrato" not in fuente:
            continue
        ent_raw = _norm(d.get("entidad_monitoreada") or d.get("entidad_declarada") or "")
        secop = d.get("secop") or {}
        unspsc = str(secop.get("unspsc","") or "")
        m = re.search(r"(\d{4})", unspsc)
        for entidad in ENTIDADES:
            if _norm(entidad)[:12] in ent_raw:
                if m and m.group(1) in CAT4:
                    por_entidad[entidad][CAT4[m.group(1)]] += 1
                    con_unspsc += 1
                else:
                    por_entidad[entidad]["Sin categoría"] += 1
                    sin_unspsc += 1
                break

print(f"Procesos con categoría UNSPSC útil: {con_unspsc}")
print(f"Procesos sin categoría: {sin_unspsc}\n")
print("Desglose por entidad (top servicios):")
for ent in ENTIDADES:
    if ent in por_entidad:
        top = por_entidad[ent].most_common(3)
        desglose = ", ".join(f"{c}:{n}" for c,n in top)
        print(f"  {ent[:25]:25} -> {desglose}")
    else:
        print(f"  {ent[:25]:25} -> sin datos")