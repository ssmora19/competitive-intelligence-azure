# -*- coding: utf-8 -*-
"""
verificar_goldcontratos.py
==========================
Revisa la tabla goldContratos y reporta qué % de contratos tiene cada campo
lleno (fecha_fin, unspsc, objeto, financiero). Sirve para saber con qué se
puede construir (alertas de vencimiento, filtro por tecnología, etc.).

Solo lee.

USO:  python verificar_goldcontratos.py
"""
from __future__ import annotations
import os, sys, json
from collections import Counter
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


def _tiene(v):
    return v is not None and str(v).strip() not in ("", "None", "no definido", "0")


def main():
    from azure.data.tables import TableServiceClient
    tsc = TableServiceClient.from_connection_string(_conn())
    tabla = tsc.get_table_client(TABLA)

    total = 0
    tak = 0
    campos = ["objeto", "unspsc", "fecha_firma", "fecha_inicio", "fecha_fin",
              "duracion", "prorrogable", "valor_pagado", "valor_pendiente"]
    con = Counter()
    # para vencimientos: contar cuántos tienen fecha_fin FUTURA
    vencen_futuro = 0
    vencen_proximo = 0   # próximos 6 meses
    ya_vencidos = 0
    ahora = datetime.now(timezone.utc)

    for e in tabla.list_entities():
        total += 1
        if e.get("es_tak") == "Sí":
            tak += 1
        for c in campos:
            if _tiene(e.get(c)):
                con[c] += 1
        # analizar fecha_fin
        ff = e.get("fecha_fin")
        if ff:
            try:
                if isinstance(ff, str):
                    fecha = datetime.fromisoformat(ff.replace("Z", "+00:00"))
                else:
                    fecha = ff
                if fecha.tzinfo is None:
                    fecha = fecha.replace(tzinfo=timezone.utc)
                dias = (fecha - ahora).days
                if dias < 0:
                    ya_vencidos += 1
                elif dias <= 180:
                    vencen_proximo += 1
                    vencen_futuro += 1
                else:
                    vencen_futuro += 1
            except Exception:
                pass

    print("=" * 60)
    print(f"goldContratos: {total} contratos ({tak} de TAK, {total-tak} competidores)")
    print("=" * 60)
    print("\n% de contratos con cada campo lleno:")
    for c in campos:
        n = con[c]
        pct = 100*n/max(total,1)
        print(f"  {c:20} {n:4}/{total} ({pct:3.0f}%)")

    print("\n" + "=" * 60)
    print("ANÁLISIS DE VENCIMIENTOS (con fecha_fin):")
    print("=" * 60)
    print(f"  Contratos vigentes (fin futuro):     {vencen_futuro}")
    print(f"  -> de esos, vencen en 6 meses:       {vencen_proximo}  <- ALERTAS")
    print(f"  Contratos ya vencidos (histórico):   {ya_vencidos}")
    print("\n  Si 'vencen en 6 meses' > 0 -> puedes hacer las alertas de vencimiento.")


if __name__ == "__main__":
    main()