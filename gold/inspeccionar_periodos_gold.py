# -*- coding: utf-8 -*-
"""
inspeccionar_periodos_gold.py
=============================
Lee las tablas Gold y muestra la distribución real de periodos: qué trimestres/
meses tienen datos y cuántos indicadores/veredictos hay en cada uno, por
contenedor. Sirve para decidir qué rango de tiempo tiene sentido mostrar en el
dashboard (y confirmar hasta qué fecha llegan los datos reales).

De solo lectura. Ejecuta (desde la carpeta gold/):  python inspeccionar_periodos_gold.py
"""
from __future__ import annotations
from collections import defaultdict, Counter

import gold_data as datos
from config_gold import TABLA_INDICADORES, TABLA_VEREDICTOS


def main():
    tabla_ind = datos._tabla(TABLA_INDICADORES)
    tabla_ver = datos._tabla(TABLA_VEREDICTOS)

    # --- Indicadores: periodos por contenedor ---
    print("\n" + "=" * 66)
    print("INDICADORES — periodos con datos por contenedor")
    print("=" * 66)
    ind_por_cont = defaultdict(Counter)
    for e in tabla_ind.list_entities():
        cont = e.get("PartitionKey", "?")
        periodo = e.get("periodo", "?")
        ind_por_cont[cont][periodo] += 1

    for cont in sorted(ind_por_cont):
        periodos = ind_por_cont[cont]
        # ordena los periodos cronológicamente (sin_fecha al final)
        orden = sorted(p for p in periodos if p != "sin_fecha")
        if "sin_fecha" in periodos:
            orden.append("sin_fecha")
        print(f"\n  {cont}:")
        for p in orden:
            print(f"      {p:12} {periodos[p]:3} filas")

    # --- Veredictos: periodos ---
    print("\n" + "=" * 66)
    print("VEREDICTOS — periodos con datos por contenedor")
    print("=" * 66)
    ver_por_cont = defaultdict(Counter)
    for e in tabla_ver.list_entities():
        cont = e.get("PartitionKey", "?")
        periodo = e.get("periodo", "?")
        ver_por_cont[cont][periodo] += 1
    for cont in sorted(ver_por_cont):
        print(f"\n  {cont}:")
        for p, n in sorted(ver_por_cont[cont].items()):
            print(f"      {p:12} {n:3} entidades")

    # --- Resumen global de periodos con datos ---
    print("\n" + "=" * 66)
    print("RESUMEN: todos los periodos que aparecen (indicadores)")
    print("=" * 66)
    todos = Counter()
    for cont, per in ind_por_cont.items():
        for p, n in per.items():
            todos[p] += n
    for p in sorted(k for k in todos if k != "sin_fecha"):
        print(f"   {p:12} {todos[p]:4} filas totales")
    if "sin_fecha" in todos:
        print(f"   {'sin_fecha':12} {todos['sin_fecha']:4} filas (sin fecha de evento)")
    print()


if __name__ == "__main__":
    main()