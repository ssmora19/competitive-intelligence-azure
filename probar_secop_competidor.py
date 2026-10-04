# -*- coding: utf-8 -*-
"""
probar_secop_competidor.py
==========================
Prueba el secop_client NUEVO consultando los contratos de UN competidor,
y muestra TODOS los campos que trae (sobre todo las fechas y el objeto),
para verificar que ahora sí trae fecha de inicio y fin.

NO toca Storage. Solo consulta SECOP y muestra en pantalla.

USO:  python probar_secop_competidor.py
      python probar_secop_competidor.py "IT CROWD S.A.S"   # nombre SECOP exacto
"""
from __future__ import annotations
import os, sys, json

# permite importar shared/ desde la raíz
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.secop_client import SecopClient


def main():
    # nombre oficial en SECOP a probar (por defecto IT CROWD, que tiene varios contratos)
    nombre_secop = sys.argv[1] if len(sys.argv) > 1 else "IT CROWD S.A.S"

    print(f"Consultando contratos de: {nombre_secop}\n")
    client = SecopClient()
    contratos = client.get_contratos_competidor(
        nombre_empresa=nombre_secop,
        limite=5,
        secop_nombres=[nombre_secop],
    )

    if not contratos:
        print("No trajo contratos. Prueba con otro nombre exacto de SECOP.")
        print('Ej: python probar_secop_competidor.py "COMWARE"')
        return

    print(f"Trajo {len(contratos)} contratos. Muestro los campos del primero:\n")
    print("=" * 66)
    primero = contratos[0]
    for campo, valor in primero.items():
        # resaltar las fechas
        marca = ""
        if "fecha" in campo.lower():
            marca = "  <-- FECHA"
        v = str(valor)[:60]
        print(f"  {campo:32} = {v}{marca}")

    print("\n" + "=" * 66)
    print("VERIFICACIÓN DE FECHAS en los 5 contratos:")
    print("=" * 66)
    for i, c in enumerate(contratos, 1):
        firma = c.get("fecha_de_firma", "—")
        inicio = c.get("fecha_de_inicio_del_contrato", "—")
        fin = c.get("fecha_de_fin_del_contrato", "—")
        objeto = (c.get("objeto_del_contrato") or "")[:45]
        print(f"\n  [{i}] {objeto}")
        print(f"      firma:  {firma}")
        print(f"      inicio: {inicio}")
        print(f"      FIN:    {fin}")

    # resumen: cuántos de los 5 traen fecha de fin
    con_fin = sum(1 for c in contratos if c.get("fecha_de_fin_del_contrato"))
    print("\n" + "=" * 66)
    print(f"  {con_fin}/{len(contratos)} contratos traen fecha de FIN")
    if con_fin > 0:
        print("  -> ¡FUNCIONA! El cliente nuevo sí trae la fecha de fin.")
    else:
        print("  -> Estos contratos no tienen fecha de fin en SECOP (defecto de la fuente).")
        print("     Prueba con otro competidor para confirmar.")


if __name__ == "__main__":
    main()