# -*- coding: utf-8 -*-
"""
diagnostico_montos.py
=====================
Revisa los contratos SECOP en silver-competidores y reporta:
  - cuántos tienen monto real (valor > 0) vs en cero/nulo
  - cuántos tienen fecha vs sin fecha
  - el detalle por competidor
Solo lee y cuenta. No modifica nada.

USO:  python diagnostico_montos.py
"""
from __future__ import annotations
import os, sys, json

CONTENEDOR = "silver-competidores"

# Solo los 5 competidores prioritarios del cliente
PRIORITARIOS = {"SETI", "IT CROWD", "COMWARE", "ITERIA", "BMIND"}


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
    from azure.storage.blob import BlobServiceClient

    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(CONTENEDOR)

    total = con_monto = monto_cero = con_fecha = sin_fecha = 0
    suma_montos = 0.0
    por_comp = {}   # competidor -> [total, con_monto, suma]

    print(f"Revisando contratos SECOP en '{CONTENEDOR}'...\n")
    for blob in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue

        # solo contratos SECOP (tienen bloque 'secop')
        secop = d.get("secop")
        if not isinstance(secop, dict):
            continue
        total += 1

        comp = d.get("entidad_declarada", "?")   # el competidor buscado
        por_comp.setdefault(comp, [0, 0, 0.0])
        por_comp[comp][0] += 1

        # monto
        try:
            valor = float(secop.get("valor") or 0)
        except (TypeError, ValueError):
            valor = 0
        if valor > 0:
            con_monto += 1
            suma_montos += valor
            por_comp[comp][1] += 1
            por_comp[comp][2] += valor
        else:
            monto_cero += 1

        # fecha
        if d.get("fecha_evento_utc"):
            con_fecha += 1
        else:
            sin_fecha += 1

    print("=" * 60)
    print("RESUMEN GENERAL")
    print("=" * 60)
    print(f"  Total contratos SECOP:      {total}")
    print(f"  Con monto real (valor > 0): {con_monto}  ({100*con_monto/total:.0f}%)" if total else "  sin datos")
    print(f"  Con monto en cero/nulo:     {monto_cero}  ({100*monto_cero/total:.0f}%)" if total else "")
    print(f"  Con fecha:                  {con_fecha}")
    print(f"  Sin fecha:                  {sin_fecha}")
    print(f"\n  SUMA TOTAL de montos:       ${suma_montos:,.0f}")

    print("\n" + "=" * 60)
    print("POR COMPETIDOR (total / con_monto / suma$)")
    print("=" * 60)
    for comp, (t, cm, s) in sorted(por_comp.items(), key=lambda x: -x[1][2]):
        marca = " *" if comp.upper() in PRIORITARIOS else ""
        print(f"  {comp[:26]:26} total={t:4} con_monto={cm:4} suma=${s:,.0f}{marca}")
    print("\n  (* = competidor prioritario del cliente)")


if __name__ == "__main__":
    main()