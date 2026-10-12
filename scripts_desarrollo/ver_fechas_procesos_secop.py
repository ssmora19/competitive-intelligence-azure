# -*- coding: utf-8 -*-
"""
ver_fechas_procesos_secop.py
Consulta el dataset de PROCESOS de SECOP (p6dx-8zbt) y muestra TODOS los campos
de fecha con sus valores, para identificar cuál es la fecha de CIERRE de la
licitación (el plazo para presentarse).
"""
import os, json, requests, time

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

# traer procesos ABIERTOS (para ver fechas de licitaciones vigentes)
params = {"$where": "adjudicado = 'No'", "$limit": 3}
auth = _auth()
print("Consultando procesos abiertos de SECOP...\n")
items = None
for i in range(4):
    try:
        r = requests.get(PROCESOS, params=params, auth=auth, timeout=60)
        r.raise_for_status()
        items = r.json()
        break
    except Exception as ex:
        print(f"  intento {i+1}: {type(ex).__name__}"); time.sleep(4)

if not items:
    print("SECOP no respondió."); raise SystemExit

print(f"Trajo {len(items)} procesos abiertos.\n")
print("=" * 70)
print("TODOS LOS CAMPOS DE FECHA (con valor):")
print("=" * 70)
for idx, it in enumerate(items):
    print(f"\n--- Proceso {idx+1}: {(it.get('descripci_n_del_procedimiento','') or '')[:45]} ---")
    for campo in sorted(it.keys()):
        cl = campo.lower()
        if "fecha" in cl or "date" in cl or "plazo" in cl or "cierre" in cl or "limite" in cl:
            print(f"  {campo:42} = {it[campo]}")
    # también mostrar estado/fase
    print(f"  --- estado/fase ---")
    for campo in ["estado_del_procedimiento", "estado_resumen", "fase", "adjudicado"]:
        if campo in it:
            print(f"  {campo:42} = {it[campo]}")