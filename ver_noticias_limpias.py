# -*- coding: utf-8 -*-
"""
ver_noticias_limpias.py
=======================
Muestra las noticias LIMPIAS que quedaron en Silver para las entidades con
veredicto importante, para ver si el detalle sirve para enriquecer las alertas
con el "porqué específico" (el hecho de la noticia).

USO:  python ver_noticias_limpias.py
      python ver_noticias_limpias.py "Accenture"
"""
from __future__ import annotations
import os, sys, json, re, unicodedata

CONTENEDORES = ["silver-competidores", "silver-clientes", "silver-clientes-competencia"]
FUENTES_NOTICIA = {"noticias_competidor", "noticias_cliente", "noticias_competencia"}
EJEMPLO = ["Accenture", "IBM Corporation", "Deloitte", "INFIHUILA", "SETI"]


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
        print("\n" + "=" * 68)
        print(f"NOTICIAS LIMPIAS DE: {entidad}")
        print("=" * 68)
        found = []
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
                if d.get("fuente_original") not in FUENTES_NOTICIA:
                    continue
                ent = _norm(d.get("entidad_monitoreada") or d.get("entidad_declarada") or "")
                if obj and obj in ent:
                    found.append({
                        "titulo": (d.get("titulo") or "")[:95],
                        "fecha": str(d.get("fecha_evento_utc") or "")[:10],
                    })
        found.sort(key=lambda x: x["fecha"], reverse=True)
        if not found:
            print("  (sin noticias)")
        for n in found[:5]:
            print(f"  [{n['fecha']}] {n['titulo']}")
        print(f"  Total: {len(found)} noticias limpias")


if __name__ == "__main__":
    main()