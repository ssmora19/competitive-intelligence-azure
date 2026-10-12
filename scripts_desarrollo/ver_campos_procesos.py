# -*- coding: utf-8 -*-
"""
ver_campos_procesos.py
======================
Consulta el dataset de PROCESOS de SECOP (p6dx-8zbt) SIN filtrar campos,
para descubrir TODOS los campos reales que existen — especialmente cómo se
llama el UNSPSC / código de categoría en procesos (que es distinto al de
contratos). Solo consulta, no toca nada.

USO:  python ver_campos_procesos.py
"""
from __future__ import annotations
import sys, time
import requests

PROCESOS = "https://www.datos.gov.co/resource/p6dx-8zbt.json"


def main():
    # traer 2 procesos cualesquiera, TODOS los campos
    params = {"$limit": 2}
    print("Consultando PROCESOS de SECOP (todos los campos)...\n")
    items = None
    for intento in range(1, 6):
        try:
            print(f"  intento {intento}/5...")
            r = requests.get(PROCESOS, params=params, timeout=90)
            r.raise_for_status()
            items = r.json()
            break
        except Exception as ex:
            print(f"     no respondió ({type(ex).__name__}); reintento en 5s...")
            time.sleep(5)
    if not items:
        print("SECOP no respondió. Reintenta luego.")
        return

    primero = items[0]
    print(f"\nTrajo {len(items)} procesos. TODOS los campos disponibles:\n")
    print("=" * 66)
    for campo in sorted(primero.keys()):
        valor = str(primero[campo])[:45]
        marca = ""
        cl = campo.lower()
        if any(x in cl for x in ["codigo", "categoria", "unspsc", "clasificac", "producto"]):
            marca = "  <<< CLASIFICACIÓN"
        if "fecha" in cl:
            marca = "  <-- FECHA"
        print(f"  {campo:42} = {valor}{marca}")

    print("\n" + "=" * 66)
    print("BUSCA arriba el campo de categoría/código (para el UNSPSC de procesos)")
    print("y los campos de fecha, para agregarlos correctamente al secop_client.")


if __name__ == "__main__":
    main()