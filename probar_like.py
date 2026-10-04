# -*- coding: utf-8 -*-
"""Compara consulta simple vs con like, para ver si el like es el problema."""
import requests, time
SECOP = "https://www.datos.gov.co/resource/jbjy-vk9h.json"

def prueba(params, etiqueta):
    for i in range(4):
        try:
            r = requests.get(SECOP, params=params, timeout=90)
            r.raise_for_status()
            n = len(r.json())
            print(f"[{etiqueta}] trajo {n}  (HTTP {r.status_code})")
            return
        except Exception as ex:
            print(f"  intento {i+1}: {type(ex).__name__}")
            time.sleep(4)
    print(f"[{etiqueta}] falló")

# 1) simple (sin where) — debe traer
prueba({"$limit": 2}, "simple sin where")
time.sleep(2)
# 2) filtro exacto por estado (sin like)
prueba({"$where": "estado_contrato = 'Cerrado'", "$limit": 2}, "where exacto (=)")
time.sleep(2)
# 3) con like sobre proveedor
prueba({"$where": "proveedor_adjudicado like '%ORACLE%'", "$limit": 2}, "like ORACLE")
time.sleep(2)
# 4) con like pero limit alto (más pesado)
prueba({"$where": "proveedor_adjudicado like '%COMWARE%'", "$limit": 50}, "like COMWARE limit 50")