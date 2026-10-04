# -*- coding: utf-8 -*-
"""Busca Comware en SECOP de varias formas para ver cuál trae resultados."""
import requests, time

SECOP = "https://www.datos.gov.co/resource/jbjy-vk9h.json"

pruebas = [
    ("proveedor_adjudicado like '%COMWARE%'", "COMWARE mayúsculas"),
    ("proveedor_adjudicado like '%Comware%'", "Comware mixto"),
    ("proveedor_adjudicado like '%comware%'", "comware minúsculas"),
    ("upper(proveedor_adjudicado) like '%COMWARE%'", "con upper()"),
    ("proveedor_adjudicado like '%ORACLE%'", "ORACLE (control: debe traer algo)"),
]

for where, etiqueta in pruebas:
    params = {"$where": where, "$limit": 3, "$select": "proveedor_adjudicado,estado_contrato"}
    ok = False
    for i in range(3):
        try:
            r = requests.get(SECOP, params=params, timeout=60)
            r.raise_for_status()
            items = r.json()
            print(f"[{etiqueta}] trajo {len(items)}")
            for it in items:
                print(f"    {it.get('proveedor_adjudicado','?')[:45]}  ({it.get('estado_contrato','?')})")
            ok = True
            break
        except Exception as ex:
            time.sleep(3)
    if not ok:
        print(f"[{etiqueta}] falló (timeout)")
    time.sleep(1)