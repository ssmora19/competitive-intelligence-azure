# -*- coding: utf-8 -*-
"""Valida las fechas en goldVentas: cuántos leads/capturas tienen fecha_senal."""
import os, sys, json

def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"): return v["AZURE_STORAGE_CONNECTION_STRING"]
    sys.exit("No hay conexión")

from azure.data.tables import TableServiceClient
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldVentas")

filas = list(t.list_entities())
leads = [e for e in filas if e.get("tipo")=="Lead"]
capturas = [e for e in filas if e.get("tipo")=="Captura"]

def resumen(grupo, nombre):
    con = [e for e in grupo if (e.get("fecha_senal","") or "")]
    sin = [e for e in grupo if not (e.get("fecha_senal","") or "")]
    print(f"\n=== {nombre} ({len(grupo)}) ===")
    print(f"  Con fecha: {len(con)} | Sin fecha: {len(sin)}")
    print("  Ejemplos CON fecha:")
    for e in sorted(con, key=lambda x: x.get("fecha_senal",""), reverse=True)[:5]:
        print(f"    [{e.get('fecha_senal','')}] {e.get('prospecto','?')[:25]:25} | {(e.get('senal','') or '')[:40]}")
    if sin:
        print("  SIN fecha (usan SECOP/general):")
        for e in sin[:3]:
            print(f"    {e.get('prospecto','?')[:25]:25} | origen: {e.get('origen','?')}")

print(f"Total prospectos: {len(filas)}")
resumen(leads, "LEADS (Tu lista de hoy)")
resumen(capturas, "CAPTURAS (Oportunidades de captura)")

# verificar que el campo existe
print("\n" + "="*50)
if filas and "fecha_senal" in filas[0]:
    print("✓ El campo 'fecha_senal' EXISTE en goldVentas")
    print("  -> En Power BI, re-expande goldVentas para traer Content.fecha_senal")
else:
    print("✗ El campo 'fecha_senal' NO está. Regenera goldVentas.")