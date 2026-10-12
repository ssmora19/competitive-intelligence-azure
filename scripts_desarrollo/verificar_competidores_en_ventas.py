# -*- coding: utf-8 -*-
"""
verificar_competidores_en_ventas.py
Revisa goldVentas y detecta si hay COMPETIDORES colados como prospectos de venta.
Cruza con companies.json (la lista de competidores).
Solo lee.
"""
import os, sys, json, re, unicodedata

def _norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).strip()

def _conn():
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    return os.environ.get("AZURE_STORAGE_CONNECTION_STRING","")

# cargar competidores
comp_path = None
for p in ["companies.json", "data/companies.json", os.path.join("..","data","companies.json")]:
    if os.path.exists(p):
        comp_path = p; break

if not comp_path:
    print("No encontré companies.json"); sys.exit(1)

with open(comp_path, encoding="utf-8") as f:
    competidores = [_norm(c["nombre"]) for c in json.load(f)]
print(f"Competidores conocidos: {len(competidores)}\n")

from azure.data.tables import TableServiceClient
tsc = TableServiceClient.from_connection_string(_conn())
tabla = tsc.get_table_client("goldVentas")

colados = []
ok = []
for e in tabla.list_entities():
    prospecto = e.get("prospecto","")
    pn = _norm(prospecto)
    es_competidor = any(c in pn or pn in c for c in competidores if len(c) > 3)
    if es_competidor:
        colados.append((prospecto, e.get("tipo","")))
    else:
        ok.append(prospecto)

print("="*56)
print(f"PROSPECTOS QUE SON COMPETIDORES (colados): {len(colados)}")
print("="*56)
for p, t in colados:
    print(f"  ⚠  {p}  [{t}]")

print(f"\nProspectos correctos (clientes/captura): {len(ok)}")