# -*- coding: utf-8 -*-
"""
diagnostico_ruido_noticias.py
=============================
Mide el ruido SOLO en las señales de tipo NOTICIA (donde está el ruido real,
como las noticias de Google asignadas a Accenture). Las redes se confían (vienen
del perfil oficial) y no se tocan.

Criterio: una noticia es VÁLIDA si el núcleo del nombre de la entidad aparece en
el título o descripción. Si no -> ruido.

Solo lee. No modifica nada.

USO:  python diagnostico_ruido_noticias.py
"""
from __future__ import annotations
import os, sys, json, re, unicodedata
from collections import defaultdict

CONTENEDORES = ["silver-competidores", "silver-clientes", "silver-clientes-competencia"]
# fuentes que son NOTICIA (el ruido vive aquí)
FUENTES_NOTICIA = {"noticias_competidor", "noticias_competencia", "noticias_cliente"}

_STOP = {"sa","sas","s","a","ltda","inc","corp","corporation","colombia","co",
         "group","grupo","de","del","la","el","los","las","y","the","consulting",
         "consultores","solutions","services","technology","tech","sistemas","latam"}


def _norm(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower())


def _nucleo(nombre: str) -> str:
    palabras = [p for p in _norm(nombre).split() if p not in _STOP and len(p) > 2]
    return " ".join(palabras).strip()


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

    total = validas = ruido = 0
    por_entidad = defaultdict(lambda: [0, 0])
    ej_ruido, ej_validas = [], []

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
            total += 1
            entidad = d.get("entidad_monitoreada") or d.get("entidad_declarada") or ""
            nucleo = _nucleo(entidad)
            texto = _norm(" ".join([str(d.get("titulo") or ""), str(d.get("descripcion") or "")]))
            if not nucleo:
                continue
            es_valida = nucleo in texto or all(p in texto.split() for p in nucleo.split())
            if es_valida:
                validas += 1
                por_entidad[entidad][0] += 1
                if len(ej_validas) < 6:
                    ej_validas.append((entidad, (d.get("titulo") or "")[:65]))
            else:
                ruido += 1
                por_entidad[entidad][1] += 1
                if len(ej_ruido) < 12:
                    ej_ruido.append((entidad, (d.get("titulo") or "")[:65]))

    print("=" * 60)
    print("RUIDO EN NOTICIAS")
    print("=" * 60)
    print(f"  Total noticias:  {total}")
    print(f"  Válidas:         {validas}")
    print(f"  RUIDO:           {ruido}")
    if total:
        print(f"  % de ruido:      {100*ruido/max(validas+ruido,1):.0f}%")

    print("\nPor entidad (validas / ruido):")
    for e, (v, r) in sorted(por_entidad.items(), key=lambda x: -x[1][1])[:12]:
        print(f"  {e[:28]:28} validas={v:3} ruido={r:3}")

    print("\n--- EJEMPLOS DE RUIDO (entidad -> título) ---")
    for e, t in ej_ruido:
        print(f"  '{e}' -> {t}")
    print("\n--- EJEMPLOS VÁLIDOS (para verificar que no se botan buenos) ---")
    for e, t in ej_validas:
        print(f"  '{e}' -> {t}")


if __name__ == "__main__":
    main()