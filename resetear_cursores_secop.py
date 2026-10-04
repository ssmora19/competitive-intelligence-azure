# -*- coding: utf-8 -*-
"""
resetear_cursores_secop.py
==========================
Resetea (borra) los cursores de delta de las fuentes SECOP, para que el
re-scraping vuelva a traer todos los contratos como NUEVOS (ya no los salta
por 'skip_delta').

Borra SOLO los cursores de fuentes que empiezan con 'secop'. No toca los
cursores de noticias, redes, etc.

La tabla de cursores suele llamarse 'controlIngesta' o 'deltaCursors'.
El script busca automáticamente la tabla correcta.

SEGURIDAD: simulacro por defecto. Solo borra con --borrar.

USO:  python resetear_cursores_secop.py            # simulacro (lista)
      python resetear_cursores_secop.py --borrar    # resetea
"""
from __future__ import annotations
import os, sys, json
from collections import Counter


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré la conexión."); sys.exit(1)


# nombres posibles de la tabla de cursores de delta
TABLAS_POSIBLES = ["controlIngesta", "deltaCursors", "controlSilver", "cursores", "delta"]


def main():
    from azure.data.tables import TableServiceClient
    borrar = "--borrar" in sys.argv
    tsc = TableServiceClient.from_connection_string(_conn())

    # listar tablas existentes
    tablas = [t.name for t in tsc.list_tables()]
    print("Tablas encontradas:", tablas, "\n")

    # buscar la tabla de cursores (la que tenga entidades con 'fuente' y hash)
    tabla_cursores = None
    for nombre in TABLAS_POSIBLES:
        if nombre in tablas:
            tabla_cursores = nombre
            break

    if not tabla_cursores:
        print("No identifiqué la tabla de cursores automáticamente.")
        print("Tablas disponibles arriba. Dime cuál es la de cursores de delta.")
        return

    print(f"Usando tabla de cursores: '{tabla_cursores}'\n")
    tabla = tsc.get_table_client(tabla_cursores)

    # buscar cursores de fuentes SECOP
    a_borrar = []
    por_fuente = Counter()
    for e in tabla.list_entities():
        # el cursor guarda la fuente en PartitionKey o en un campo 'fuente'
        pk = str(e.get("PartitionKey", ""))
        fuente = str(e.get("fuente", "") or pk)
        if "secop" in fuente.lower() or "secop" in pk.lower():
            a_borrar.append(e)
            por_fuente[fuente if "secop" in fuente.lower() else pk] += 1

    print("=" * 56)
    print("CURSORES SECOP encontrados:")
    print("=" * 56)
    for f, n in por_fuente.most_common():
        print(f"  {f:40} {n}")
    print(f"\n  TOTAL a resetear: {len(a_borrar)}")

    if not borrar:
        print("\n[SIMULACRO] No borré nada. Corre con --borrar para resetear.")
        return

    n = 0
    for e in a_borrar:
        try:
            tabla.delete_entity(partition_key=e["PartitionKey"], row_key=e["RowKey"])
            n += 1
        except Exception as ex:
            print(f"  error: {ex}")
    print(f"\nListo. Reseteé {n} cursores de SECOP. Ahora re-scrapea y traerá todo como nuevo.")


if __name__ == "__main__":
    main()