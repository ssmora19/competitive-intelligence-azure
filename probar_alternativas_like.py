# -*- coding: utf-8 -*-
"""Prueba alternativas al like '%x%' que sí funcionen con datos.gov.co actual."""
import requests, time
SECOP = "https://www.datos.gov.co/resource/jbjy-vk9h.json"

def prueba(params, etiqueta):
    for i in range(3):
        try:
            r = requests.get(SECOP, params=params, timeout=90)
            r.raise_for_status()
            items = r.json()
            print(f"[{etiqueta}] trajo {len(items)}")
            if items:
                print(f"    ej: {items[0].get('proveedor_adjudicado','?')[:40]}")
            return len(items)
        except Exception as ex:
            print(f"  intento {i+1}: {type(ex).__name__} {str(ex)[:60]}")
            time.sleep(4)
    return -1

# A) $q búsqueda de texto completo (Socrata full-text)
prueba({"$q": "ORACLE", "$limit": 3}, "A) $q ORACLE (full-text)")
time.sleep(2)
# B) like en MAYÚSCULAS
prueba({"$where": "proveedor_adjudicado LIKE '%ORACLE%'", "$limit": 3}, "B) LIKE mayúsculas")
time.sleep(2)
# C) starts_with
prueba({"$where": "starts_with(proveedor_adjudicado, 'ORACLE')", "$limit": 3}, "C) starts_with ORACLE")
time.sleep(2)
# D) = exacto con nombre completo conocido
prueba({"$where": "proveedor_adjudicado = 'ORACLE COLOMBIA LIMITADA'", "$limit": 3}, "D) = exacto")
time.sleep(2)
# E) $q con COMWARE
prueba({"$q": "COMWARE", "$limit": 3}, "E) $q COMWARE")