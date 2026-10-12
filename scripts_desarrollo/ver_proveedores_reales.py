# -*- coding: utf-8 -*-
"""Trae contratos reales y muestra nombres de proveedores, luego prueba buscar uno."""
import requests, time
SECOP = "https://www.datos.gov.co/resource/jbjy-vk9h.json"

# 1) traer 10 contratos cualesquiera y ver los nombres de proveedores
print("=== 10 proveedores reales (sin filtro) ===")
r = requests.get(SECOP, params={"$limit": 10, "$select": "proveedor_adjudicado,estado_contrato"}, timeout=90)
provs = []
for it in r.json():
    p = it.get("proveedor_adjudicado", "?")
    provs.append(p)
    print(f"  {p[:50]}  ({it.get('estado_contrato','?')})")

# 2) tomar el primer proveedor real y buscarlo con like
if provs:
    objetivo = provs[0]
    print(f"\n=== Buscar '{objetivo}' de varias formas ===")
    time.sleep(2)
    # like con el nombre completo real
    for where, et in [
        (f"proveedor_adjudicado like '%{objetivo}%'", "like nombre completo"),
        (f"proveedor_adjudicado = '{objetivo}'", "= exacto"),
    ]:
        for i in range(3):
            try:
                rr = requests.get(SECOP, params={"$where": where, "$limit": 3, "$select": "proveedor_adjudicado"}, timeout=90)
                rr.raise_for_status()
                print(f"  [{et}] trajo {len(rr.json())}")
                break
            except Exception as ex:
                print(f"    intento {i+1}: {str(ex)[:70]}")
                time.sleep(4)
        time.sleep(2)