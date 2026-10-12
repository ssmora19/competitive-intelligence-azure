# -*- coding: utf-8 -*-
"""Verifica si COMWARE realmente tiene contratos, probando variaciones."""
import requests, time
SECOP = "https://www.datos.gov.co/resource/jbjy-vk9h.json"

pruebas = [
    "proveedor_adjudicado like '%COMWARE%'",
    "proveedor_adjudicado like '%Comware%'",
    "proveedor_adjudicado like '%COMWARE S.A%'",
    "proveedor_adjudicado like '%ORACLE%'",
    "proveedor_adjudicado like '%IBM%'",
    "proveedor_adjudicado like '%MICROSOFT%'",
    "proveedor_adjudicado like '%INDRA%'",
]
for where in pruebas:
    for i in range(3):
        try:
            r = requests.get(SECOP, params={"$where": where, "$limit": 3,
                             "$select": "proveedor_adjudicado,estado_contrato,fecha_de_firma"}, timeout=90)
            r.raise_for_status()
            items = r.json()
            print(f"[{where[:45]}] -> {len(items)}")
            for it in items:
                print(f"      {it.get('proveedor_adjudicado','?')[:40]}")
            break
        except Exception as ex:
            time.sleep(4)
    time.sleep(1.5)