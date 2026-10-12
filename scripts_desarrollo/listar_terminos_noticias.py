# -*- coding: utf-8 -*-
"""
listar_terminos_noticias.py
===========================
Lista los TÉRMINOS DE BÚSQUEDA que se usaron para las noticias, con cuántas
señales trajo cada uno y cuántas fueron ruido (no mencionan la entidad). Así
vemos qué términos son problemáticos y hay que afinar.

También revisa el companies.json y los JSON de entidades para ver el campo de
query configurado hoy.

Solo lee. No modifica nada.

USO:  python listar_terminos_noticias.py
"""
from __future__ import annotations
import os, sys, json, re, unicodedata
from collections import defaultdict

CONTENEDORES = ["silver-competidores", "silver-clientes", "silver-clientes-competencia"]
FUENTES_NOTICIA = {"noticias_competidor", "noticias_competencia", "noticias_cliente"}

# Rutas de los archivos de entidades (para ver los queries configurados)
ARCHIVOS_ENTIDADES = [
    os.path.join("data", "companies.json"),
    os.path.join("ingestion_clientes", "clientes.json"),
    os.path.join("ingestion_clientes_competencia", "clientes_competencia.json"),
]

_STOP = {"sa","sas","s","ltda","inc","corp","corporation","colombia","co","group",
         "grupo","de","del","la","el","los","las","y","the","consulting","consultores",
         "solutions","services","technology","tech","sistemas","latam"}


def _norm(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower())


def _nucleo(nombre: str) -> str:
    return " ".join(p for p in _norm(nombre).split() if p not in _STOP and len(p) > 2).strip()


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

    # término/entidad -> [total, ruido]
    stats = defaultdict(lambda: [0, 0])
    for cont in CONTENEDORES:
        try:
            cli = bs.get_container_client(cont)
            blobs = list(cli.list_blobs())
        except Exception:
            continue
        for blob in blobs:
            try:
                d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
            except Exception:
                continue
            if d.get("fuente_original") not in FUENTES_NOTICIA:
                continue
            entidad = d.get("entidad_monitoreada") or d.get("entidad_declarada") or "?"
            termino = d.get("termino_busqueda") or entidad
            clave = f"{entidad}  ||  término: {termino}"
            nucleo = _nucleo(entidad)
            texto = _norm(" ".join([str(d.get("titulo") or ""), str(d.get("descripcion") or "")]))
            stats[clave][0] += 1
            if nucleo and not (nucleo in texto or all(p in texto.split() for p in nucleo.split())):
                stats[clave][1] += 1

    print("=" * 72)
    print("TÉRMINOS DE BÚSQUEDA DE NOTICIAS (ordenados por más ruido)")
    print("=" * 72)
    for clave, (tot, ru) in sorted(stats.items(), key=lambda x: -x[1][1]):
        pct = 100*ru/tot if tot else 0
        print(f"  ruido {ru:3}/{tot:3} ({pct:3.0f}%)  {clave}")

    # Revisar los queries configurados en los archivos de entidades
    print("\n" + "=" * 72)
    print("QUERIES CONFIGURADOS EN LOS ARCHIVOS DE ENTIDADES")
    print("=" * 72)
    for ruta in ARCHIVOS_ENTIDADES:
        if not os.path.exists(ruta):
            print(f"\n  ({ruta} no encontrado)")
            continue
        print(f"\n  --- {ruta} ---")
        try:
            with open(ruta, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:
            print(f"    error: {ex}"); continue
        for c in data[:40]:
            nombre = c.get("nombre") or c.get("entidad") or "?"
            q = c.get("noticias_query") or c.get("query_noticias") or c.get("noticias") or "(sin query definido)"
            print(f"    {nombre[:30]:30} -> {q}")


if __name__ == "__main__":
    main()