# -*- coding: utf-8 -*-
"""
limpiar_nombres_entidad.py
==========================
Renombra los nombres de entidad TÉCNICOS (mercado_colombia, fabricantes_tak...)
a versiones legibles para el tablero, en la columna entidad_nombre de
goldVeredictos.

SEGURIDAD: simulacro por defecto. Solo aplica con --aplicar.

USO:  python limpiar_nombres_entidad.py            # simulacro (muestra qué cambiaría)
      python limpiar_nombres_entidad.py --aplicar    # aplica los cambios
"""
from __future__ import annotations
import os, sys, json

TABLA = "goldVeredictos"

# Mapa: nombre técnico -> nombre legible
RENOMBRAR = {
    "mercado_colombia":    "Mercado colombiano",
    "mercado_global":      "Mercado global",
    "fabricantes_tak":     "Fabricantes / Proveedores",
    "ecosistema_colombia": "Ecosistema TI Colombia",
    "innovacion_colombia": "Innovación en Colombia",
}


def _conn() -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json"),
              os.path.join("..", "..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING."); sys.exit(1)


def main() -> None:
    from azure.data.tables import TableServiceClient, UpdateMode
    aplicar = "--aplicar" in sys.argv
    tsc = TableServiceClient.from_connection_string(_conn())
    tabla = tsc.get_table_client(TABLA)

    cambios = []
    for e in tabla.list_entities():
        actual = e.get("entidad_nombre", "")
        if actual in RENOMBRAR:
            cambios.append((e, actual, RENOMBRAR[actual]))

    print("=" * 56)
    print("CAMBIOS DE NOMBRE")
    print("=" * 56)
    if not cambios:
        print("  No hay nombres técnicos para renombrar.")
        return
    for _, viejo, nuevo in cambios:
        print(f"  '{viejo}'  ->  '{nuevo}'")
    print(f"\n  Total veredictos a renombrar: {len(cambios)}")

    if not aplicar:
        print("\n[SIMULACRO] No apliqué nada. Corre con --aplicar para renombrar.")
        return

    n = 0
    for e, _, nuevo in cambios:
        e["entidad_nombre"] = nuevo
        tabla.update_entity(e, mode=UpdateMode.MERGE)
        n += 1
    print(f"\nListo. Renombré {n} veredictos. Refresca Power BI.")


if __name__ == "__main__":
    main()