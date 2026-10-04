# -*- coding: utf-8 -*-
"""
ver_unspsc_silver.py
====================
Revisa los códigos UNSPSC de los contratos en Silver y los agrupa por familia
(los primeros 4 dígitos), mostrando cuántos hay de cada una y ejemplos de objeto.
Sirve para decidir qué familias UNSPSC son "de TI / portafolio TAK".

Solo lee.

USO:  python ver_unspsc_silver.py
"""
from __future__ import annotations
import os, sys, json, re
from collections import defaultdict

CONTENEDORES = ["silver-competidores", "silver-clientes-competencia"]
FUENTES = {"secop_competidor", "secop_competencia_contratos", "secop_competencia_procesos"}


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


# Familias UNSPSC conocidas de TI (segmento 43=TI, 81=servicios prof. incl. TI)
FAMILIAS_TI = {
    "4321": "Hardware/computadores", "4322": "Redes/telecom", "4323": "Software",
    "4320": "Componentes electrónicos", "8111": "Servicios TI/desarrollo",
    "8112": "Servicios datos/internet", "8110": "Servicios TI general",
    "4318": "Equipos comunicación", "5211": "Periféricos/impresoras",
}


def main():
    from azure.storage.blob import BlobServiceClient
    bs = BlobServiceClient.from_connection_string(_conn())

    por_familia = defaultdict(lambda: [0, ""])   # familia -> [conteo, ejemplo_objeto]
    total = 0

    for cont in CONTENEDORES:
        try:
            cli = bs.get_container_client(cont)
            blobs = list(cli.list_blobs())
        except Exception:
            continue
        for b in blobs:
            try:
                d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
            except Exception:
                continue
            if d.get("fuente_original") not in FUENTES:
                continue
            secop = d.get("secop") or {}
            unspsc = str(secop.get("unspsc") or "")
            # extraer los dígitos: V1.81111808 -> 8111
            m = re.search(r"(\d{4})", unspsc)
            if not m:
                continue
            total += 1
            fam = m.group(1)
            por_familia[fam][0] += 1
            if not por_familia[fam][1]:
                por_familia[fam][1] = (secop.get("objeto") or d.get("titulo") or "")[:45]

    print("=" * 72)
    print(f"FAMILIAS UNSPSC en los contratos ({total} con código)")
    print("=" * 72)
    print(f"  {'familia':8} {'conteo':>7}  {'¿TI?':6} {'ejemplo de objeto'}")
    print("  " + "-" * 68)
    for fam, (n, ej) in sorted(por_familia.items(), key=lambda x: -x[1][0]):
        es_ti = FAMILIAS_TI.get(fam, "")
        marca = "TI ✓" if es_ti else ""
        etiqueta = f"({es_ti})" if es_ti else ""
        print(f"  {fam:8} {n:>7}  {marca:6} {ej} {etiqueta}")

    print("\n" + "=" * 72)
    print("Las familias marcadas 'TI ✓' son las del portafolio TAK.")
    print("Revisa si hay otras familias con muchos contratos que también sean de TI.")


if __name__ == "__main__":
    main()