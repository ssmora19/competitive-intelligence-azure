# -*- coding: utf-8 -*-
"""
diagnostico_ruido_senales.py
============================
Mide el RUIDO en las señales de texto: cuántas realmente mencionan a su entidad
monitoreada vs cuántas son ruido (contenido que no la menciona). Reporta el %
de ruido total, por contenedor y por fuente. Solo lee, no modifica nada.

Criterio: una señal es VÁLIDA si el nombre de la entidad (o su núcleo) aparece
en el título o la descripción. Si no, es RUIDO.

USO:  python diagnostico_ruido_senales.py
"""
from __future__ import annotations
import os, sys, json, re, unicodedata
from collections import defaultdict

# Contenedores de texto (no SECOP, que ya validamos aparte)
CONTENEDORES = [
    "silver-competidores", "silver-clientes", "silver-clientes-competencia",
]

# Palabras genéricas que NO ayudan a identificar (para sacar el núcleo del nombre)
_STOP = {"sa","sas","s","a","ltda","inc","corp","corporation","colombia","co",
         "group","grupo","de","del","la","el","los","las","y","the","consulting",
         "consultores","solutions","services","technology","tech","sistemas"}


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

    total = validas = ruido = sin_entidad = 0
    por_fuente = defaultdict(lambda: [0, 0])   # fuente -> [validas, ruido]
    ejemplos_ruido = []

    for cont in CONTENEDORES:
        try:
            cli = bs.get_container_client(cont)
            blobs = list(cli.list_blobs())
        except Exception:
            continue
        print(f"Revisando {cont} ({len(blobs)} señales)...")
        for blob in blobs:
            try:
                d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
            except Exception:
                continue
            # solo señales de texto (no SECOP)
            if isinstance(d.get("secop"), dict):
                continue
            total += 1
            entidad = d.get("entidad_monitoreada") or d.get("entidad_declarada") or ""
            fuente = d.get("fuente_original", "?")
            nucleo = _nucleo(entidad)
            texto = _norm(" ".join([str(d.get("titulo") or ""), str(d.get("descripcion") or "")]))

            if not nucleo:
                sin_entidad += 1
                continue
            # válida si el núcleo del nombre aparece en el texto
            if nucleo in texto or all(p in texto.split() for p in nucleo.split()):
                validas += 1
                por_fuente[fuente][0] += 1
            else:
                ruido += 1
                por_fuente[fuente][1] += 1
                if len(ejemplos_ruido) < 12:
                    ejemplos_ruido.append((entidad, fuente, (d.get("titulo") or "")[:60]))

    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"  Total señales de texto:  {total}")
    print(f"  Válidas (mencionan entidad): {validas}")
    print(f"  RUIDO (no la mencionan):     {ruido}")
    print(f"  Sin entidad clara:           {sin_entidad}")
    if total:
        print(f"  % de ruido:                  {100*ruido/max(validas+ruido,1):.0f}%")

    print("\n" + "=" * 60)
    print("POR FUENTE (válidas / ruido)")
    print("=" * 60)
    for f, (v, r) in sorted(por_fuente.items(), key=lambda x: -x[1][1]):
        print(f"  {f[:28]:28} validas={v:4} ruido={r:4}")

    print("\n" + "=" * 60)
    print("EJEMPLOS DE RUIDO (entidad -> título)")
    print("=" * 60)
    for ent, fue, tit in ejemplos_ruido:
        print(f"  '{ent}' [{fue}] -> {tit}")


if __name__ == "__main__":
    main()