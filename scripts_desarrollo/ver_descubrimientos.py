# -*- coding: utf-8 -*-
"""Inventario de 'descubrimientos' reales del sistema para Ventas (datos duros)."""
import os, sys, json
from collections import defaultdict

def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"): return v["AZURE_STORAGE_CONNECTION_STRING"]
    sys.exit("No hay conexión")

from azure.data.tables import TableServiceClient
tsc = TableServiceClient.from_connection_string(_conn())
tv = tsc.get_table_client("goldVentas")
filas = list(tv.list_entities())

print("=" * 65)
print("DESCUBRIMIENTO 1: Entidades más activas en SECOP (n_secop)")
print("=" * 65)
print("(Cuánto contrata cada entidad en compra pública — NO se ve en webs)")
activos = sorted([e for e in filas if (e.get("n_secop",0) or 0) > 0], key=lambda x: -(x.get("n_secop",0) or 0))
for e in activos[:8]:
    print(f"  {e.get('prospecto','?')[:28]:28} {e.get('n_secop',0):3} procesos  [{e.get('tipo','?')}]")

print("\n" + "=" * 65)
print("DESCUBRIMIENTO 2: Prospectos con señal de NOTICIA reciente")
print("=" * 65)
print("(Hechos recientes detectados automáticamente — valor de oportunidad)")
con_noticia = [e for e in filas if e.get("origen")=="noticia" and (e.get("fecha_senal","") or "")]
con_noticia.sort(key=lambda x: x.get("fecha_senal",""), reverse=True)
for e in con_noticia[:6]:
    print(f"  [{e.get('fecha_senal','')}] {e.get('prospecto','?')[:22]:22} {(e.get('senal','') or '')[:38]}")

print("\n" + "=" * 65)
print("DESCUBRIMIENTO 3: Reparto de prospectos por competidor (capturas)")
print("=" * 65)
print("(De qué competidor son los prospectos capturables + su actividad)")
por_comp = defaultdict(lambda: {"n":0, "secop":0})
for e in filas:
    if e.get("tipo")=="Captura" and e.get("competidor"):
        c = e["competidor"]
        por_comp[c]["n"] += 1
        por_comp[c]["secop"] += (e.get("n_secop",0) or 0)
for c, d in sorted(por_comp.items(), key=lambda x: -x[1]["n"]):
    print(f"  {c[:25]:25} {d['n']} prospectos, {d['secop']} procesos SECOP totales")

print("\n" + "=" * 65)
print("RESUMEN para decidir qué graficar:")
print("=" * 65)
print(f"  Entidades activas en SECOP: {len(activos)}")
print(f"  Prospectos con noticia reciente: {len(con_noticia)}")
print(f"  Competidores con clientes capturables: {len(por_comp)}")