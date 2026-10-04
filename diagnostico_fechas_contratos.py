# -*- coding: utf-8 -*-
"""
diagnostico_fechas_contratos.py
===============================
Revisa los contratos SECOP en silver-competidores y silver-clientes-competencia
para ver QUÉ FECHAS traen y cuántas están completas. Clave para decidir qué es
viable de: línea de tiempo, alertas de vencimiento, histórico de renovaciones.

Busca todos los campos de fecha posibles en el bloque secop y en el raw.
Solo lee.

USO:  python diagnostico_fechas_contratos.py
"""
from __future__ import annotations
import os, sys, json
from collections import Counter

CONTENEDORES = ["silver-competidores", "silver-clientes-competencia", "silver-clientes"]


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


def _buscar_fechas(d, prefijo=""):
    """Encuentra recursivamente todas las claves que parezcan fecha y si tienen valor."""
    encontradas = {}
    if isinstance(d, dict):
        for k, v in d.items():
            kl = k.lower()
            if any(p in kl for p in ["fecha", "date", "vigencia", "plazo", "termin", "inicio", "fin", "firma", "publicac", "cierre"]):
                tiene = v is not None and str(v).strip() not in ("", "null", "None")
                encontradas[prefijo + k] = tiene
            # recursión en subdicts
            if isinstance(v, dict):
                encontradas.update(_buscar_fechas(v, prefijo + k + "."))
    return encontradas


def main():
    from azure.storage.blob import BlobServiceClient
    bs = BlobServiceClient.from_connection_string(_conn())

    total = 0
    campos_fecha = Counter()      # campo -> cuántas veces aparece
    campos_con_valor = Counter()  # campo -> cuántas veces tiene valor
    ejemplos = {}

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
            secop = d.get("secop")
            if not isinstance(secop, dict):
                continue
            total += 1
            # buscar fechas en todo el documento
            fechas = _buscar_fechas(d)
            for campo, tiene in fechas.items():
                campos_fecha[campo] += 1
                if tiene:
                    campos_con_valor[campo] += 1
                    if campo not in ejemplos:
                        # sacar el valor de ejemplo
                        partes = campo.split(".")
                        val = d
                        try:
                            for p in partes:
                                val = val[p]
                            ejemplos[campo] = str(val)[:30]
                        except Exception:
                            pass

    print("=" * 66)
    print(f"CONTRATOS SECOP REVISADOS: {total}")
    print("=" * 66)
    print("\nCAMPOS DE FECHA ENCONTRADOS (campo -> con valor / total, %):")
    for campo in sorted(campos_fecha, key=lambda c: -campos_con_valor[c]):
        con = campos_con_valor[campo]
        tot = campos_fecha[campo]
        pct = 100*con/tot if tot else 0
        ej = ejemplos.get(campo, "")
        print(f"  {campo[:38]:38} {con:4}/{tot:4} ({pct:3.0f}%)  ej: {ej}")

    print("\n" + "=" * 66)
    print("LECTURA:")
    print("  - Campos con % alto = fechas confiables (se puede hacer timeline).")
    print("  - Campos con % bajo o 0 = no sirven para línea de tiempo.")


if __name__ == "__main__":
    main()