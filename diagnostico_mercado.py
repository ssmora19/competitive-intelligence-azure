# -*- coding: utf-8 -*-
"""
diagnostico_mercado.py
======================
Revisa los contenedores de Silver relacionados con MERCADO / DEMANDA / TENDENCIAS
para saber qué datos hay disponibles y con qué podemos construir la sección
Mercado del tablero SIN repetir competidores.

Revisa: silver-tendencias, silver-mercado, silver-capacidades, silver-proveedores.
Reporta: cuántos registros, de qué fuentes, y muestra ejemplos de contenido.

Solo lee. No modifica nada.

USO:  python diagnostico_mercado.py
"""
from __future__ import annotations
import os, sys, json
from collections import Counter

CONTENEDORES = [
    "silver-tendencias",
    "silver-mercado",
    "silver-capacidades",
    "silver-proveedores",
]


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


def revisar(cli, contenedor):
    print("\n" + "=" * 66)
    print(f"  {contenedor}")
    print("=" * 66)
    try:
        c = cli.get_container_client(contenedor)
        blobs = list(c.list_blobs())
    except Exception as ex:
        print(f"  (no existe o no se pudo abrir: {ex})")
        return

    total = len(blobs)
    print(f"  Total registros: {total}")
    if total == 0:
        return

    fuentes = Counter()
    familias = Counter()
    tipos_senal = Counter()
    ejemplos = []
    # revisar hasta 300 para no tardar demasiado
    for b in blobs[:300]:
        try:
            d = json.loads(c.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        fuentes[d.get("fuente_original", "?")] += 1
        familias[d.get("familia", "?")] += 1
        clasif = d.get("clasificacion", {}) or {}
        tipos_senal[clasif.get("tipo_senal", "?")] += 1
        if len(ejemplos) < 6:
            ejemplos.append({
                "titulo": (d.get("titulo") or "")[:70],
                "familia": d.get("familia", ""),
                "termino": d.get("termino_busqueda", ""),
            })

    print(f"\n  Fuentes: {dict(fuentes)}")
    print(f"  Familias: {dict(familias)}")
    print(f"  Tipos de señal: {dict(tipos_senal)}")
    print(f"\n  Ejemplos de contenido:")
    for e in ejemplos:
        extra = f" | búsqueda: {e['termino']}" if e['termino'] else ""
        print(f"    - [{e['familia']}] {e['titulo']}{extra}")


def main() -> None:
    from azure.storage.blob import BlobServiceClient
    bs = BlobServiceClient.from_connection_string(_conn())
    print("Diagnóstico de datos para la sección MERCADO...")
    for cont in CONTENEDORES:
        revisar(bs, cont)


if __name__ == "__main__":
    main()