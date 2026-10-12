# -*- coding: utf-8 -*-
"""
ver_fechas_vencimiento.py
=========================
Muestra las fechas de fin reales de los contratos de competidores por vencer,
para verificar si de verdad todas vencen el mismo día o si hay un problema
en el cálculo. Solo lee.

USO:  python ver_fechas_vencimiento.py
"""
from __future__ import annotations
import os, sys, json
from datetime import datetime, timezone

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
    tsc = TableServiceClient.from_connection_string(_conn())
    tabla = tsc.get_table_client(TABLA)

    ahora = datetime.now(timezone.utc)
    por_vencer = []
    for e in tabla.list_entities():
        if e.get("es_tak") == "Sí":
            continue
        ff = e.get("fecha_fin")
        if not ff:
            continue
        try:
            if isinstance(ff, str):
                fecha = datetime.fromisoformat(ff.replace("Z", "+00:00"))
            else:
                fecha = ff
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
            dias = (fecha - ahora).days
            if 0 <= dias <= 180:
                por_vencer.append((dias, str(ff)[:10], e.get("competidor","?"),
                                   (e.get("objeto","") or "")[:40]))
        except Exception as ex:
            print(f"  error parseando {ff}: {ex}")

    por_vencer.sort()
    print("=" * 70)
    print(f"CONTRATOS DE COMPETIDORES POR VENCER (próximos 180 días): {len(por_vencer)}")
    print("=" * 70)
    print(f"  {'días':>5}  {'fecha_fin':12}  {'competidor':20}  objeto")
    print("  " + "-" * 66)
    for dias, fecha, comp, obj in por_vencer:
        print(f"  {dias:>5}  {fecha:12}  {comp[:20]:20}  {obj}")

    # verificar si las fechas son iguales o distintas
    fechas_unicas = set(f for _, f, _, _ in por_vencer)
    print(f"\n  Fechas de fin DISTINTAS: {len(fechas_unicas)}")
    if len(fechas_unicas) == 1:
        print("  !! TODAS tienen la misma fecha_fin -> hay un problema en los datos.")
    else:
        print("  -> Las fechas son distintas. Si el tablero muestra días iguales,")
        print("     el problema está en el cálculo DAX (revisar 'Dias Para Vencer').")


if __name__ == "__main__":
    main()