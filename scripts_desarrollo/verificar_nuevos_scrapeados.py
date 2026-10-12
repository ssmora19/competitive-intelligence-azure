# -*- coding: utf-8 -*-
"""Verifica cuáles de las entidades/competidores nuevos ya tienen datos en Bronze."""
import os, sys, json
from collections import Counter

def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"): return v["AZURE_STORAGE_CONNECTION_STRING"]
    sys.exit("No hay conexión")

from azure.storage.blob import BlobServiceClient
bs = BlobServiceClient.from_connection_string(_conn())

# entidades nuevas a buscar
nuevos_clientes = [
    "Cancillería", "Comisión Nacional del Servicio Civil", "CREMIL", "ETB", "ICBF",
    "Fuerza Aérea", "Ministerio de Transporte", "Ministerio de Defensa", "Los Cobos",
    "Politécnico Grancolombiano", "Transmilenio", "Ministerio de Hacienda",
    "Ministerio de las Culturas", "RCN", "Agencia Nacional de Tierras",
    "Consejo Nacional Electoral", "Defensoría del Pueblo", "Departamento Nacional de Planeación",
    "Fondo Nacional del Ahorro", "FOGACOOP", "ICETEX", "INFOTIC",
    "Procuraduría", "Registraduría", "Ministerio TIC", "Ministerio de Ciencias",
]
nuevos_competidores = ["Global Technology Services", "Grow Data"]

# contar blobs por empresa en bronze-clientes-competencia
print("=" * 60)
print("CLIENTES NUEVOS con datos en bronze-clientes-competencia:")
print("=" * 60)
cli = bs.get_container_client("bronze-clientes-competencia")
encontrados = Counter()
for b in cli.list_blobs():
    nombre = b.name.lower()
    for ent in nuevos_clientes:
        if ent.lower().split()[0] in nombre or ent.lower() in nombre:
            encontrados[ent] += 1
            break
con_datos = 0
for ent in nuevos_clientes:
    n = encontrados.get(ent, 0)
    estado = f"{n} blobs" if n else "SIN DATOS"
    if n: con_datos += 1
    print(f"  {ent[:40]:40} {estado}")
print(f"\n  Con datos: {con_datos}/{len(nuevos_clientes)}")

# competidores nuevos
print("\n" + "=" * 60)
print("COMPETIDORES NUEVOS con datos en bronze-competidores:")
print("=" * 60)
cli2 = bs.get_container_client("bronze-competidores")
enc2 = Counter()
for b in cli2.list_blobs():
    nombre = b.name.lower()
    for comp in nuevos_competidores:
        if comp.lower().split()[0] in nombre:
            enc2[comp] += 1
            break
for comp in nuevos_competidores:
    n = enc2.get(comp, 0)
    print(f"  {comp:35} {n} blobs" if n else f"  {comp:35} SIN DATOS")