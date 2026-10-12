# -*- coding: utf-8 -*-
"""
ver_licitaciones_cierre.py
==========================
QUÉ HACE: lista las licitaciones de goldLicitaciones con su fecha de cierre, los
días que faltan (contados por FECHA de Colombia, no por horas) y el enlace a SECOP,
para verificar a mano que las fechas son reales. Solo lee. Uso local (no se despliega).

USO:  python ver_licitaciones_cierre.py
"""
import os
import sys
import json
from collections import Counter
from datetime import datetime, timezone, timedelta

from azure.data.tables import TableServiceClient

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    return json.load(open("local.settings.json", encoding="utf-8"))["Values"]["AZURE_STORAGE_CONNECTION_STRING"]


hoy = (datetime.now(timezone.utc) - timedelta(hours=5)).date()      # fecha de Colombia
filas = list(TableServiceClient.from_connection_string(_conn())
             .get_table_client("goldLicitaciones").list_entities())
lista = []
for e in filas:
    cierre = str(e.get("fecha_cierre") or "")[:10]
    try:
        dias = (datetime.strptime(cierre, "%Y-%m-%d").date() - hoy).days
    except ValueError:
        dias = None
    lista.append((dias if dias is not None else 9999, cierre, e))
lista.sort(key=lambda x: x[0])

print(f"Hoy (Colombia): {hoy}   |   Licitaciones en goldLicitaciones: {len(lista)}\n")
for dias, cierre, e in lista:
    print(f"{cierre}  ({dias:>3} días)  {str(e.get('entidad', ''))[:45]}")
    print(f"      {str(e.get('objeto', ''))[:110]}")
    print(f"      {e.get('url', '') or '(sin enlace)'}\n")

dist = Counter("vencida" if d < 0 else "0-2 días" if d <= 2 else "3-7 días" if d <= 7
               else "8-30 días" if d <= 30 else "31-90 días" for d, _, _ in lista)
print("Distribución por plazo de cierre:")
for k in ("vencida", "0-2 días", "3-7 días", "8-30 días", "31-90 días"):
    print(f"  {k:12} {dist.get(k, 0)}")