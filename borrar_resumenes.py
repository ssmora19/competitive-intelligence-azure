# -*- coding: utf-8 -*-
"""
borrar_resumenes.py
===================
Uso único: borra la columna `resumen_ia` de los veredictos que la tengan, para
poder regenerarlos con un prompt nuevo. Reanudable no aplica aquí: es un limpiador.

CÓMO SE EJECUTA (desde donde ve tu local.settings.json)
    python borrar_resumenes.py           # muestra cuántos borraría (SIMULACRO, no borra)
    python borrar_resumenes.py --borrar  # borra de verdad
"""

from __future__ import annotations
import os
import sys
import json

TABLA = "goldVeredictos"


def _conn() -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json"),
              os.path.join("..", "..", "local.settings.json"),
              os.path.join("..", "..", "..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING."); sys.exit(1)


def main() -> None:
    from azure.data.tables import TableServiceClient, UpdateMode

    borrar = "--borrar" in sys.argv
    tsc = TableServiceClient.from_connection_string(_conn())
    tabla = tsc.get_table_client(TABLA)

    entidades = list(tabla.list_entities())
    con_resumen = [e for e in entidades if e.get("resumen_ia")]
    print(f"Veredictos con resumen_ia: {len(con_resumen)}")

    if not borrar:
        print("SIMULACRO: no borré nada. Corre con --borrar para borrar de verdad.")
        for e in con_resumen:
            print(f"  - {e.get('entidad_nombre', '')}")
        return

    for e in con_resumen:
        e.pop("resumen_ia", None)  # quitar la columna del todo
        tabla.update_entity(e, mode=UpdateMode.REPLACE)
        print(f"  borrado: {e.get('entidad_nombre', '')}")


if __name__ == "__main__":
    main()