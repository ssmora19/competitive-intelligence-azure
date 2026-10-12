# -*- coding: utf-8 -*-
"""
probar_filtro_comware.py
Prueba dos consultas a SECOP para Comware: SIN filtro de estado y CON filtro,
para ver qué estados tiene y por qué el filtro deja 0.
"""
import requests, time

SECOP = "https://www.datos.gov.co/resource/jbjy-vk9h.json"

def consulta(where, etiqueta):
    params = {"$where": where, "$limit": 8,
              "$select": "referencia_del_contrato,estado_contrato,fecha_de_firma"}
    for i in range(3):
        try:
            r = requests.get(SECOP, params=params, timeout=60)
            r.raise_for_status()
            items = r.json()
            print(f"\n=== {etiqueta} ===")
            print(f"  where: {where}")
            print(f"  trajo: {len(items)} contratos")
            for it in items:
                print(f"    estado={it.get('estado_contrato','?'):18} firma={it.get('fecha_de_firma','—')}")
            return
        except Exception as ex:
            print(f"  intento {i+1} falló: {ex}"); time.sleep(3)

# 1) SIN filtro de estado
consulta("proveedor_adjudicado like '%COMWARE%'", "SIN filtro de estado")

# 2) CON el filtro actual (!=)
consulta("(proveedor_adjudicado like '%COMWARE%') AND estado_contrato != 'Borrador' AND estado_contrato != 'Cancelado'",
         "CON filtro != (el actual)")

# 3) CON filtro alternativo (NOT IN)
consulta("(proveedor_adjudicado like '%COMWARE%') AND estado_contrato NOT IN ('Borrador','Cancelado')",
         "CON filtro NOT IN (alternativa)")