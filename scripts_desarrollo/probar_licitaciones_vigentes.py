# -*- coding: utf-8 -*-
"""
probar_licitaciones_vigentes.py
Verifica si SECOP tiene licitaciones REALMENTE vigentes: publicadas hace poco
Y con fecha de recepción de ofertas en el FUTURO (que aún se pueden ganar).
"""
import os, json, requests, time
from datetime import datetime, timezone, timedelta

PROCESOS = "https://www.datos.gov.co/resource/p6dx-8zbt.json"

def _auth():
    kid = os.getenv("SOCRATA_API_KEY_ID"); ksec = os.getenv("SOCRATA_API_KEY_SECRET")
    if not (kid and ksec):
        for r in ("local.settings.json", os.path.join("..","local.settings.json")):
            if os.path.exists(r):
                v = json.load(open(r,encoding="utf-8")).get("Values",{})
                kid = kid or v.get("SOCRATA_API_KEY_ID"); ksec = ksec or v.get("SOCRATA_API_KEY_SECRET")
                break
    return (kid, ksec) if kid and ksec else None

auth = _auth()
hoy = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00.000")
hace_60 = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT00:00:00.000")

print(f"Hoy: {hoy[:10]}\n")

# PRUEBA 1: procesos publicados en los últimos 60 días
print("=" * 60)
print("PRUEBA 1: Procesos publicados en los últimos 60 días")
print("=" * 60)
p1 = {"$where": f"fecha_de_publicacion_del >= '{hace_60}'", "$limit": 5,
      "$select": "descripci_n_del_procedimiento,fecha_de_publicacion_del,fecha_de_recepcion_de,adjudicado,estado_resumen,codigo_principal_de_categoria"}
for i in range(4):
    try:
        r = requests.get(PROCESOS, params=p1, auth=auth, timeout=60)
        r.raise_for_status()
        items = r.json()
        print(f"Trajo {len(items)} procesos recientes:")
        for it in items:
            print(f"  pub: {str(it.get('fecha_de_publicacion_del',''))[:10]} | recepcion: {str(it.get('fecha_de_recepcion_de',''))[:10]} | {(it.get('descripci_n_del_procedimiento','') or '')[:40]}")
        break
    except Exception as ex:
        print(f"  intento {i+1}: {ex}"); time.sleep(4)

time.sleep(2)

# PRUEBA 2: procesos con fecha de recepción FUTURA (licitaciones que cierran pronto)
print("\n" + "=" * 60)
print("PRUEBA 2: Con fecha de recepción de ofertas FUTURA (vigentes)")
print("=" * 60)
p2 = {"$where": f"fecha_de_recepcion_de >= '{hoy}'", "$limit": 5,
      "$select": "descripci_n_del_procedimiento,fecha_de_publicacion_del,fecha_de_recepcion_de,codigo_principal_de_categoria"}
for i in range(4):
    try:
        r = requests.get(PROCESOS, params=p2, auth=auth, timeout=60)
        r.raise_for_status()
        items = r.json()
        print(f"Trajo {len(items)} licitaciones que cierran en el futuro:")
        for it in items:
            print(f"  cierra: {str(it.get('fecha_de_recepcion_de',''))[:10]} | {(it.get('descripci_n_del_procedimiento','') or '')[:45]}")
        break
    except Exception as ex:
        print(f"  intento {i+1}: {ex}"); time.sleep(4)