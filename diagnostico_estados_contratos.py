# -*- coding: utf-8 -*-
"""
diagnostico_estados_contratos.py
================================
Consulta contratos de varios competidores en SECOP y los agrupa por ESTADO,
mostrando cuántos de cada estado tienen datos COMPLETOS (objeto + fecha de fin).
Sirve para decidir qué estados filtrar (borradores vacíos) y cuáles conservar.

Solo consulta SECOP, no toca Storage.

USO:  python diagnostico_estados_contratos.py
"""
from __future__ import annotations
import os, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared.secop_client import SecopClient

# Nombres SECOP de algunos competidores prioritarios (ajusta si quieres)
COMPETIDORES = {
    "COMWARE": ["COMWARE"],
    "IT CROWD": ["IT CROWD S.A.S"],
    "SETI": ["Servicios Especializados de Tecnologia E Informatica"],
    "Indra": ["INDRA"],
    "IZE Consultores": ["IZE Consultores"],
}


def main():
    client = SecopClient()

    # estado -> [total, con_objeto, con_fecha_fin, con_ambos]
    por_estado = defaultdict(lambda: [0, 0, 0, 0])
    total = 0

    for comp, nombres in COMPETIDORES.items():
        print(f"Consultando {comp}...")
        contratos = client.get_contratos_competidor(comp, limite=50, secop_nombres=nombres)
        for c in contratos:
            total += 1
            estado = (c.get("estado_contrato") or "Sin estado").strip()
            objeto = (c.get("objeto_del_contrato") or "").strip()
            fin = c.get("fecha_de_fin_del_contrato")
            tiene_obj = objeto and objeto.lower() not in ("no definido", "sin descripcion", "")
            tiene_fin = bool(fin)
            por_estado[estado][0] += 1
            if tiene_obj: por_estado[estado][1] += 1
            if tiene_fin: por_estado[estado][2] += 1
            if tiene_obj and tiene_fin: por_estado[estado][3] += 1
        time.sleep(1)

    print("\n" + "=" * 74)
    print(f"CONTRATOS POR ESTADO  (total: {total})")
    print("=" * 74)
    print(f"  {'ESTADO':28} {'total':>6} {'c/objeto':>9} {'c/fecha_fin':>12} {'COMPLETOS':>10}")
    print("  " + "-" * 70)
    for estado, (tot, obj, fin, ambos) in sorted(por_estado.items(), key=lambda x: -x[1][0]):
        print(f"  {estado[:28]:28} {tot:>6} {obj:>9} {fin:>12} {ambos:>10}")

    print("\n" + "=" * 74)
    print("LECTURA:")
    print("  - Estados con muchos COMPLETOS = quédatelos (tienen objeto y fecha).")
    print("  - Estados con casi 0 completos (ej. Borrador) = candidatos a filtrar.")
    print("  - 'En ejecución'/'Modificado' con fecha de fin futura = para alertas de vencimiento.")


if __name__ == "__main__":
    main()