# -*- coding: utf-8 -*-
"""
ver_senales_prospectos.py
=========================
Para los prospectos de venta, muestra QUÉ FUENTES de señal tienen en Silver
(SECOP, web, redes, noticias...), para poder enriquecer el gancho de las
'oportunidades generales' (las que no tienen noticia).

Solo lee.

USO:  python ver_senales_prospectos.py
      python ver_senales_prospectos.py "Universidad Militar"
"""
from __future__ import annotations
import os, sys, json, re, unicodedata
from collections import Counter

CONTENEDORES = [
    "silver-clientes", "silver-clientes-competencia", "silver-competidores",
]
# entidades de ejemplo (las que salieron como "oportunidad general")
EJEMPLO = ["Universidad Militar", "RUNT", "COLVATEL", "SOFT Management", "RED COMPUTO"]


def _norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower())


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
    from azure.storage.blob import BlobServiceClient
    bs = BlobServiceClient.from_connection_string(_conn())
    entidades = [sys.argv[1]] if len(sys.argv) > 1 else EJEMPLO

    for entidad in entidades:
        obj = _norm(entidad)
        print("\n" + "=" * 62)
        print(f"SEÑALES DE: {entidad}")
        print("=" * 62)
        fuentes = Counter()
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
                ent = _norm(re.sub(r"\(cliente de.*?\)", "", d.get("entidad_monitoreada") or d.get("entidad_declarada") or "", flags=re.IGNORECASE))
                if obj and obj in ent:
                    fuente = d.get("fuente_original", "?")
                    fuentes[fuente] += 1
                    if fuente not in ejemplos:
                        ejemplos[fuente] = (d.get("titulo") or "")[:65]
        if not fuentes:
            print("  (sin señales)")
        for f, n in fuentes.most_common():
            print(f"  {f:28} {n:3}  ej: {ejemplos.get(f,'')}")


if __name__ == "__main__":
    main()