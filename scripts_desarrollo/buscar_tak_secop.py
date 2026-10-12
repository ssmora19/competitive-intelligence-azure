# -*- coding: utf-8 -*-
"""
buscar_tak_secop.py
===================
Busca los contratos de TAK (Tech & Knowledge) directamente en SECOP II,
para saber si TAK aparece como proveedor y con cuántos contratos/montos.
Solo consulta la API pública de SECOP. No modifica nada.

USO:  python buscar_tak_secop.py
      python buscar_tak_secop.py "NOMBRE EXACTO EN SECOP"   # buscar un nombre puntual
"""
from __future__ import annotations
import sys, requests

SECOP = "https://www.datos.gov.co/resource/jbjy-vk9h.json"

# Nombres posibles de TAK en SECOP (ajústalos si conoces el exacto)
NOMBRES_TAK = [
    "TECH AND KNOWLEDGE",
    "TECH & KNOWLEDGE",
    "TECH KNOWLEDGE",
    "TAK",
]


def buscar(nombre: str):
    where = f"proveedor_adjudicado like '%{nombre}%'"
    params = {
        "$where": where,
        "$limit": 50,
        "$select": "referencia_del_contrato,nombre_entidad,objeto_del_contrato,"
                   "valor_del_contrato,estado_contrato,proveedor_adjudicado,fecha_de_firma",
    }
    try:
        r = requests.get(SECOP, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as ex:
        print(f"  error buscando '{nombre}': {ex}")
        return []


def main():
    nombres = [sys.argv[1]] if len(sys.argv) > 1 else NOMBRES_TAK

    print("Buscando contratos de TAK en SECOP...\n")
    vistos = set()
    total = 0
    suma = 0.0
    for nombre in nombres:
        print(f"--- Buscando: '{nombre}' ---")
        items = buscar(nombre)
        print(f"    {len(items)} resultados")
        for it in items:
            ref = it.get("referencia_del_contrato", "")
            if ref in vistos:
                continue
            vistos.add(ref)
            prov = it.get("proveedor_adjudicado", "")
            try:
                val = float(it.get("valor_del_contrato") or 0)
            except (TypeError, ValueError):
                val = 0
            total += 1
            suma += val
            print(f"      ${val:,.0f} | {prov[:35]} | {it.get('nombre_entidad','')[:35]}")
            print(f"         objeto: {(it.get('objeto_del_contrato') or '')[:60]}")

    print("\n" + "=" * 60)
    print(f"  TOTAL contratos de TAK encontrados: {total}")
    print(f"  SUMA de montos: ${suma:,.0f}")
    if total == 0:
        print("\n  TAK NO aparece con estos nombres. Prueba con el nombre exacto:")
        print("    python buscar_tak_secop.py \"NOMBRE COMO APARECE EN SECOP\"")


if __name__ == "__main__":
    main()