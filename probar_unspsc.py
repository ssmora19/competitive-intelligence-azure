# -*- coding: utf-8 -*-
"""
probar_unspsc.py
================
Verifica qué campos de clasificación (UNSPSC / código de producto) trae la API
de SECOP II Contratos, y qué tan llenos vienen. El UNSPSC dice QUÉ se compró
(categoría de producto/servicio) aunque el 'objeto' esté vacío.

Consulta contratos de IT/tecnología y muestra TODOS los campos de un registro,
resaltando los de clasificación.
Solo consulta SECOP, no toca Storage.

USO:  python probar_unspsc.py
"""
from __future__ import annotations
import sys
import requests

SECOP = "https://www.datos.gov.co/resource/jbjy-vk9h.json"


def main():
    # Traer unos contratos que mencionen Oracle (para que sean de TI y tengan datos)
    # SIN $select -> trae TODOS los campos, para descubrir cuáles existen
    params = {
        "$where": "descripcion_del_proceso like '%oracle%' OR objeto_del_contrato like '%oracle%'",
        "$limit": 3,
        "$order": "fecha_de_firma DESC",
    }
    print("Consultando SECOP (contratos con 'oracle')...\n")
    try:
        r = requests.get(SECOP, params=params, timeout=40)
        r.raise_for_status()
        items = r.json()
    except Exception as ex:
        print(f"Error: {ex}")
        return

    if not items:
        print("No trajo resultados. Reintenta (datos.gov.co es intermitente).")
        return

    print(f"Trajo {len(items)} contratos. TODOS los campos del primero:\n")
    print("=" * 68)
    primero = items[0]
    for campo in sorted(primero.keys()):
        valor = str(primero[campo])[:55]
        # resaltar campos de clasificación / producto / código
        marca = ""
        cl = campo.lower()
        if any(x in cl for x in ["unspsc", "codigo", "clasificac", "producto", "categoria", "bien", "servicio"]):
            marca = "  <<< CLASIFICACIÓN"
        print(f"  {campo:42} = {valor}{marca}")

    # revisar específicamente si hay campos tipo UNSPSC
    print("\n" + "=" * 68)
    print("CAMPOS QUE PARECEN CLASIFICACIÓN DE PRODUCTO:")
    print("=" * 68)
    encontrado = False
    for campo in primero.keys():
        cl = campo.lower()
        if any(x in cl for x in ["unspsc", "codigo_de", "clasificac", "producto", "categoria"]):
            encontrado = True
            # mostrar el valor en los 3 contratos
            print(f"\n  Campo: {campo}")
            for i, it in enumerate(items, 1):
                print(f"    [{i}] {str(it.get(campo, '—'))[:60]}")
    if not encontrado:
        print("  No encontré campos UNSPSC obvios. Revisa la lista completa de arriba")
        print("  por si el código viene con otro nombre.")


if __name__ == "__main__":
    main()