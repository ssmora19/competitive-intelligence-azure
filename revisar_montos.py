# -*- coding: utf-8 -*-
"""
revisar_montos.py
=================
Examina los contratos con montos más ALTOS y más BAJOS de silver-competidores,
para entender si los montos gigantes son reales o errores de parseo.
Solo lee. No modifica nada.

USO:  python revisar_montos.py
"""
from __future__ import annotations
import os, sys, json

CONTENEDOR = "silver-competidores"


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

    contratos = []
    for blob in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        secop = d.get("secop")
        if not isinstance(secop, dict):
            continue
        try:
            valor = float(secop.get("valor") or 0)
        except (TypeError, ValueError):
            valor = 0
        contratos.append({
            "competidor": d.get("entidad_declarada", "?"),
            "valor": valor,
            "entidad": secop.get("entidad_contratante", "?"),
            "tipo": secop.get("tipo_contrato", "?"),
            "estado": secop.get("estado", "?"),
            "objeto": (d.get("titulo") or "")[:70],
        })

    contratos.sort(key=lambda x: -x["valor"])

    # Distribución por rangos de monto
    rangos = {
        "≥ 1 billón (10^12)": 0,
        "100 mil M – 1 billón": 0,
        "10 mil M – 100 mil M": 0,
        "1000 M – 10 mil M": 0,
        "100 M – 1000 M": 0,
        "< 100 M": 0,
    }
    for c in contratos:
        v = c["valor"]
        if v >= 1e12: rangos["≥ 1 billón (10^12)"] += 1
        elif v >= 1e11: rangos["100 mil M – 1 billón"] += 1
        elif v >= 1e10: rangos["10 mil M – 100 mil M"] += 1
        elif v >= 1e9: rangos["1000 M – 10 mil M"] += 1
        elif v >= 1e8: rangos["100 M – 1000 M"] += 1
        else: rangos["< 100 M"] += 1

    print("=" * 70)
    print("DISTRIBUCIÓN POR RANGO DE MONTO")
    print("=" * 70)
    for r, n in rangos.items():
        print(f"  {r:26} {n:4} contratos")

    print("\n" + "=" * 70)
    print("TOP 12 CONTRATOS MÁS GRANDES (¿reales o error?)")
    print("=" * 70)
    for c in contratos[:12]:
        print(f"\n  ${c['valor']:,.0f}  | {c['competidor']}")
        print(f"     tipo: {c['tipo']} | estado: {c['estado']}")
        print(f"     entidad: {c['entidad'][:50]}")
        print(f"     objeto: {c['objeto']}")


if __name__ == "__main__":
    main()