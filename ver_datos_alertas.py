# -*- coding: utf-8 -*-
"""Verifica qué datos hay para cada tipo de alerta del centro de inteligencia."""
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

from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient
tsc = TableServiceClient.from_connection_string(_conn())
bs = BlobServiceClient.from_connection_string(_conn())

print("=" * 60)
print("DATOS DISPONIBLES PARA CADA TIPO DE ALERTA")
print("=" * 60)

# 1) Movimientos de competidores (contratos con fecha)
try:
    t = tsc.get_table_client("goldContratos")
    n_comp = sum(1 for e in t.list_entities() if e.get("es_tak")=="No" and e.get("fecha_firma"))
    print(f"\n1. MOVIMIENTOS COMPETIDORES (contratos ganados con fecha): {n_comp}")
except Exception as ex:
    print(f"\n1. goldContratos: error {ex}")

# 2) Licitaciones urgentes
try:
    t = tsc.get_table_client("goldLicitaciones")
    n_lic = sum(1 for e in t.list_entities())
    print(f"2. LICITACIONES URGENTES (abiertas): {n_lic}")
except Exception as ex:
    print(f"2. goldLicitaciones: no existe aún ({ex})")

# 3) Regulatorio — buscar en silver-regulatorio o similar
print(f"\n3. REGULATORIO — buscando contenedores...")
for cont in ["silver-regulatorio", "silver-normativo"]:
    try:
        cli = bs.get_container_client(cont)
        n = sum(1 for _ in cli.list_blobs())
        print(f"   {cont}: {n} registros")
    except Exception:
        print(f"   {cont}: no existe")
# buscar noticias regulatorias en mercado
try:
    cli = bs.get_container_client("silver-mercado")
    reg = 0
    for b in list(cli.list_blobs())[:200]:
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
            titulo = (d.get("titulo","") or "").lower()
            if any(x in titulo for x in ["ley", "decreto", "norma", "regula", "mintic", "resolucion", "circular"]):
                reg += 1
        except Exception:
            continue
    print(f"   noticias regulatorias en silver-mercado: ~{reg}")
except Exception as ex:
    print(f"   silver-mercado: {ex}")

# 4) Señales de clientes (goldVentas)
try:
    t = tsc.get_table_client("goldVentas")
    n_v = sum(1 for _ in t.list_entities())
    print(f"\n4. SEÑALES DE CLIENTES (goldVentas): {n_v}")
except Exception as ex:
    print(f"\n4. goldVentas: {ex}")