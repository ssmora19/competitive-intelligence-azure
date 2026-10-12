# -*- coding: utf-8 -*-
"""Cuenta veredictos por umbral de score, para ajustar el filtro de la Alerta."""
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
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldVeredictos")

filas = list(t.list_entities())
con_resumen = [e for e in filas if (e.get("resumen_ia","") or "") and (e.get("entidad_nombre","") or "")]

umbrales = [0.60, 0.50, 0.40, 0.33, 0.25, 0.0]
print(f"Total veredictos: {len(filas)} | con resumen_ia y nombre: {len(con_resumen)}\n")
print("Cuántos superan cada umbral de score:")
for u in umbrales:
    n = sum(1 for e in con_resumen if float(e.get("score",0) or 0) >= u)
    print(f"  score >= {u}: {n}")

# top 5 por score
print("\nTop 5 por score (los que mostraría la Alerta):")
top = sorted(con_resumen, key=lambda x: float(x.get("score",0) or 0), reverse=True)[:5]
for e in top:
    print(f"  {float(e.get('score',0) or 0):.2f}  {e.get('entidad_nombre','?')[:25]:25} [{e.get('tipo_veredicto','?')}]")