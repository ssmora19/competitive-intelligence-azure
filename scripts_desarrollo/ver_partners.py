# -*- coding: utf-8 -*-
"""Muestra cómo quedaron separados partner_actual vs partner_nuevo en el radar."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldRadarTecnologias")

print("=" * 80)
print("PARTNERS: actuales (ya tiene) vs nuevos (aliarse)")
print("=" * 80)
for e in sorted(t.list_entities(), key=lambda x: x.get("orden",0)):
    tec = e.get("tecnologia","?")
    actual = e.get("partner_actual","") or "—"
    nuevo = e.get("partner_nuevo","") or "—"
    ver = (e.get("veredicto","") or "")[:15]
    print(f"\n  {tec}  [{ver}]")
    print(f"    Ya trabaja con: {actual}")
    print(f"    Aliarse con (nuevo): {nuevo}")