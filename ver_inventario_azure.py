# -*- coding: utf-8 -*-
"""
ver_inventario_azure.py
=======================
QUÉ HACE: inventario REAL de lo que hay en stinteligencia, para sustentar el
presupuesto con datos medidos (solo lee, uso local; no se despliega: ver_*):
  1. Contenedores: cuántos archivos (blobs) tiene cada uno, cuánto pesan y
     cuál es el más antiguo y el más reciente.
  2. Tablas: cuántas filas tiene cada una y su peso aproximado.
  3. Corridas del orquestador: cuántos pasos tuvo cada una y cuántos minutos
     de trabajo efectivo sumaron (lo que cobra Azure Functions).

USO:  python ver_inventario_azure.py inventario.txt
"""
import os
import sys
import json
from collections import defaultdict

from azure.storage.blob import BlobServiceClient
from azure.data.tables import TableServiceClient

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

salida = []


def p(t=""):
    print(t)
    salida.append(t)


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    return json.load(open("local.settings.json", encoding="utf-8"))["Values"]["AZURE_STORAGE_CONNECTION_STRING"]


def mb(n):
    return f"{n / 1024 / 1024:,.2f} MB"


conn = _conn()

# ── 1. CONTENEDORES ──
p("=" * 92)
p("1. CONTENEDORES (archivos JSON de Bronze y Silver)")
p("=" * 92)
p(f"{'contenedor':32} {'archivos':>9} {'peso':>12} {'más antiguo':>12} {'más reciente':>13}")
bs = BlobServiceClient.from_connection_string(conn)
tot_n = tot_b = 0
for c in sorted(bs.list_containers(), key=lambda x: x.name):
    if c.name.startswith(("azure-", "$")):
        continue
    n = b = 0
    viejo = nuevo = None
    for blob in bs.get_container_client(c.name).list_blobs():
        n += 1
        b += blob.size or 0
        f = blob.last_modified
        viejo = f if viejo is None or f < viejo else viejo
        nuevo = f if nuevo is None or f > nuevo else nuevo
    tot_n += n
    tot_b += b
    p(f"{c.name:32} {n:>9,} {mb(b):>12} {str(viejo)[:10] if viejo else '-':>12} {str(nuevo)[:10] if nuevo else '-':>13}")
p(f"{'TOTAL':32} {tot_n:>9,} {mb(tot_b):>12}")

# ── 2. TABLAS ──
p("\n" + "=" * 92)
p("2. TABLAS (control, registros y tablas Gold del tablero)")
p("=" * 92)
p(f"{'tabla':32} {'filas':>9} {'peso aprox.':>12}")
ts = TableServiceClient.from_connection_string(conn)
tot_f = tot_tb = 0
for t in sorted(ts.list_tables(), key=lambda x: x.name):
    filas = peso = 0
    for e in ts.get_table_client(t.name).list_entities():
        filas += 1
        peso += len(json.dumps({k: str(v) for k, v in e.items()}, ensure_ascii=False).encode("utf-8"))
    tot_f += filas
    tot_tb += peso
    p(f"{t.name:32} {filas:>9,} {mb(peso):>12}")
p(f"{'TOTAL':32} {tot_f:>9,} {mb(tot_tb):>12}")

# ── 3. CORRIDAS DEL ORQUESTADOR ──
p("\n" + "=" * 92)
p("3. CORRIDAS DEL ORQUESTADOR (minutos de trabajo efectivo = lo que cobra Functions)")
p("=" * 92)
corr = defaultdict(lambda: {"pasos": 0, "seg": 0, "bronze": 0, "silver": 0, "scripts": 0, "ini": "", "fin": ""})
try:
    for e in ts.get_table_client("pipelineLog").list_entities():
        c = corr[e["PartitionKey"]]
        c["pasos"] += 1
        c["seg"] += int(e.get("segundos", 0) or 0)
        paso = str(e.get("paso", ""))
        c["bronze" if paso.startswith("Bronze") else "silver" if paso.startswith("Silver") else "scripts"] += 1
        f = str(e.get("fecha", ""))
        c["ini"] = f if not c["ini"] or f < c["ini"] else c["ini"]
        c["fin"] = f if f > c["fin"] else c["fin"]
except Exception as ex:
    p(f"(sin pipelineLog: {ex})")
p(f"{'corrida':18} {'pasos':>6} {'bronze':>7} {'silver':>7} {'scripts':>8} {'min. efectivos':>15}  inicio → fin")
for k in sorted(corr)[-8:]:
    c = corr[k]
    p(f"{k:18} {c['pasos']:>6} {c['bronze']:>7} {c['silver']:>7} {c['scripts']:>8} {c['seg'] / 60:>15,.1f}  {c['ini'][:16]} → {c['fin'][11:16]}")

if len(sys.argv) > 1:
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        f.write("\n".join(salida))
    print(f"\nGuardado en '{sys.argv[1]}'.")