# -*- coding: utf-8 -*-
"""
borrar_tabla_contratos.py
=========================
Borra COMPLETAMENTE la tabla goldContratos (todas las filas viejas + nuevas).
Úsalo antes de regenerar, para que no se acumulen contratos de corridas
anteriores. Después corre: python generar_contratos.py --escribir

USO:  python borrar_tabla_contratos.py            # simulacro (cuenta)
      python borrar_tabla_contratos.py --borrar    # borra la tabla entera
"""
from __future__ import annotations
import os, sys, json

TABLA = "goldContratos"


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


def main():
    from azure.data.tables import TableServiceClient
    borrar = "--borrar" in sys.argv
    tsc = TableServiceClient.from_connection_string(_conn())
    tabla = tsc.get_table_client(TABLA)

    # contar filas
    n = 0
    for _ in tabla.list_entities():
        n += 1
    print(f"La tabla '{TABLA}' tiene {n} filas.")

    if not borrar:
        print("[SIMULACRO] No borré nada. Corre con --borrar para borrar la tabla entera.")
        return

    # borrar fila por fila (más confiable que delete_table para evitar el bloqueo)
    print(f"Borrando las {n} filas...")
    borradas = 0
    for e in tabla.list_entities():
        try:
            tabla.delete_entity(partition_key=e["PartitionKey"], row_key=e["RowKey"])
            borradas += 1
        except Exception as ex:
            print(f"  error: {ex}")
    print(f"Listo. Borré {borradas} filas. La tabla '{TABLA}' quedó vacía.")
    print("Ahora corre: python generar_contratos.py --escribir")


if __name__ == "__main__":
    main()