# -*- coding: utf-8 -*-
"""
ver_detalle_veredictos.py
=========================
Muestra el campo 'detalle' (el JSON de factores) de un veredicto de cada TIPO,
para entender qué datos concretos hay disponibles para explicar el "por qué"
de cada alerta. Solo lee.

USO:  python ver_detalle_veredictos.py
"""
from __future__ import annotations
import os, sys, json

TABLA = "goldVeredictos"


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
    from azure.data.tables import TableServiceClient
    tsc = TableServiceClient.from_connection_string(_conn())
    tabla = tsc.get_table_client(TABLA)

    vistos = set()
    for e in tabla.list_entities():
        tipo = e.get("tipo_veredicto", "?")
        # un ejemplo por tipo, preferir score alto
        if tipo in vistos:
            continue
        try:
            score = float(e.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        if score < 0.5 and tipo not in ("alerta_cambio", "alerta_regulatoria", "gap_capacidad"):
            continue
        vistos.add(tipo)
        print("\n" + "=" * 68)
        print(f"TIPO: {tipo}  |  Entidad: {e.get('entidad_nombre','?')}  |  score: {score:.2f}")
        print(f"  categoria: {e.get('categoria','?')}")
        print(f"  n_senales: {e.get('n_senales','?')}")
        detalle = e.get("detalle", "")
        # el detalle es un JSON en texto; lo mostramos formateado
        try:
            d = json.loads(detalle)
            print("  detalle (factores):")
            print(json.dumps(d, indent=4, ensure_ascii=False)[:900])
        except Exception:
            print(f"  detalle (crudo): {str(detalle)[:400]}")

    print(f"\n\nTipos encontrados: {sorted(vistos)}")


if __name__ == "__main__":
    main()