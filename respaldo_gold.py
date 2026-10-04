# -*- coding: utf-8 -*-
"""
respaldo_gold.py
================
QUÉ HACE: guarda una copia de TODAS las tablas gold* de stinteligencia en una
carpeta local (un .json por tabla), y si hace falta las restaura desde esa copia.

PARA QUÉ SIRVE: red de seguridad antes del primer despliegue. Los scripts del
tablero borran y reescriben sus tablas; si una corrida en Azure sale mal, el
tablero se recupera restaurando el respaldo.

USO (desde la raíz del proyecto, con el .venv activo):
    python respaldo_gold.py --respaldar
        -> crea la carpeta respaldo_gold_AAAAMMDD_HHMM con un .json por tabla

    python respaldo_gold.py --restaurar respaldo_gold_AAAAMMDD_HHMM
        -> SIMULACRO: muestra qué restauraría, sin tocar nada

    python respaldo_gold.py --restaurar respaldo_gold_AAAAMMDD_HHMM --confirmar
        -> restaura de verdad (borra cada tabla y la recrea con el respaldo)

Este script es SOLO para uso local: no se despliega a Azure (.funcignore: respaldo_*).
"""
import os
import sys
import json
import time
from datetime import datetime

from azure.data.tables import TableServiceClient, UpdateMode


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    if os.path.exists("local.settings.json"):
        v = json.load(open("local.settings.json", encoding="utf-8")).get("Values", {})
        if v.get("AZURE_STORAGE_CONNECTION_STRING"):
            return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING (ni en el entorno ni en local.settings.json).")
    sys.exit(1)


def _valor(v):
    """Convierte cualquier valor de Table Storage a algo que JSON pueda guardar."""
    if hasattr(v, "value"):                 # EntityProperty (p.ej. enteros de 64 bits)
        v = v.value
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def respaldar():
    tsc = TableServiceClient.from_connection_string(_conn())
    tablas = sorted(t.name for t in tsc.list_tables() if t.name.startswith("gold"))
    if not tablas:
        print("No hay tablas gold* que respaldar.")
        return
    carpeta = "respaldo_gold_" + datetime.now().strftime("%Y%m%d_%H%M")
    os.makedirs(carpeta, exist_ok=True)
    total = 0
    for nombre in tablas:
        filas = [{k: _valor(v) for k, v in e.items()}
                 for e in tsc.get_table_client(nombre).list_entities()]
        with open(os.path.join(carpeta, f"{nombre}.json"), "w", encoding="utf-8") as f:
            json.dump(filas, f, ensure_ascii=False, indent=1)
        print(f"  {nombre:32} {len(filas):6} filas")
        total += len(filas)
    print(f"\nRespaldo listo en '{carpeta}': {len(tablas)} tablas, {total} filas.")


def restaurar(carpeta, confirmar):
    if not os.path.isdir(carpeta):
        print(f"No existe la carpeta '{carpeta}'.")
        sys.exit(1)
    archivos = sorted(a for a in os.listdir(carpeta) if a.endswith(".json"))
    tsc = TableServiceClient.from_connection_string(_conn())
    for archivo in archivos:
        nombre = archivo[:-5]
        filas = json.load(open(os.path.join(carpeta, archivo), encoding="utf-8"))
        if not confirmar:
            print(f"  [SIMULACRO] restauraría {nombre:32} {len(filas):6} filas")
            continue
        try:
            tsc.delete_table(nombre)
        except Exception:
            pass
        tabla = None
        for _ in range(12):                 # Azure tarda en liberar una tabla borrada
            try:
                tabla = tsc.create_table_if_not_exists(nombre)
                break
            except Exception as ex:
                if "BeingDeleted" in str(ex) or "Conflict" in str(ex):
                    time.sleep(15)
                else:
                    raise
        if tabla is None:
            print(f"  {nombre}: Azure no liberó la tabla; reintenta en un minuto.")
            continue
        for fila in filas:
            fila.pop("Timestamp", None)
            tabla.upsert_entity(fila, mode=UpdateMode.REPLACE)
        print(f"  restaurada {nombre:32} {len(filas):6} filas")
    if not confirmar:
        print("\nSIMULACRO: no se tocó nada. Agrega --confirmar para restaurar de verdad.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--respaldar"]:
        respaldar()
    elif args[:1] == ["--restaurar"] and len(args) >= 2:
        restaurar(args[1], "--confirmar" in args)
    else:
        print(__doc__)