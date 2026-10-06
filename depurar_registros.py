# -*- coding: utf-8 -*-
"""
depurar_registros.py
====================
QUÉ HACE: borra las filas antiguas de las tablas de REGISTRO (bitácoras), que
son las únicas que crecen sin límite:
    executionLogs        -> una fila por cada fuente de Bronze en cada corrida
    executionLogsSilver  -> una fila por cada tanda de Silver
    executionLogsGold    -> una fila por cada cálculo de Gold
    pipelineLog          -> una fila por cada paso del orquestador
Las tablas Gold (tablero) no se tocan: se reescriben completas en cada corrida.
Las tablas de control (controlIngesta, pipelineEstado) tampoco: no crecen.

PARA QUÉ SIRVE: política de retención de datos. Los archivos de Bronze y Silver
los borra la política de ciclo de vida de Azure; las tablas no la soportan,
por eso se depuran con este script, que el orquestador ejecuta al final de
cada corrida.

USO:
    python depurar_registros.py              # SIMULACRO: solo cuenta qué borraría
    python depurar_registros.py --escribir   # borra de verdad
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta

from azure.data.tables import TableServiceClient

DIAS_RETENCION = 180          # se conservan 6 meses de bitácoras
TABLAS = ["executionLogs", "executionLogsSilver", "executionLogsGold", "pipelineLog"]


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    return json.load(open("local.settings.json", encoding="utf-8"))["Values"]["AZURE_STORAGE_CONNECTION_STRING"]


def main():
    escribir = "--escribir" in sys.argv
    limite = (datetime.now(timezone.utc) - timedelta(days=DIAS_RETENCION)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ts = TableServiceClient.from_connection_string(_conn())
    existentes = {t.name for t in ts.list_tables()}
    total = 0
    print(f"Depuración de registros anteriores a {limite[:10]} ({DIAS_RETENCION} días)"
          f"{'' if escribir else '  [SIMULACRO]'}")
    for nombre in TABLAS:
        if nombre not in existentes:
            print(f"  {nombre:22} no existe, se omite")
            continue
        t = ts.get_table_client(nombre)
        viejas = list(t.query_entities(f"Timestamp lt datetime'{limite}'",
                                       select=["PartitionKey", "RowKey"]))
        if escribir:
            for e in viejas:
                try:
                    t.delete_entity(partition_key=e["PartitionKey"], row_key=e["RowKey"])
                except Exception:
                    pass
        print(f"  {nombre:22} {len(viejas):6} filas {'borradas' if escribir else 'se borrarían'}")
        total += len(viejas)
    print(f"Listo. Total: {total} filas{'' if escribir else ' (simulacro: agrega --escribir para borrar)'}.")


if __name__ == "__main__":
    main()