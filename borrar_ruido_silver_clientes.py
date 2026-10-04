# -*- coding: utf-8 -*-
"""
borrar_ruido_silver_clientes.py
===============================
Borra de silver-clientes SOLO las noticias RUIDOSAS (las que no mencionan a su
entidad ni a sus alias), conservando las buenas. Mismo criterio con alias que
clientes-competencia.

SEGURIDAD: simulacro por defecto + ejemplos. Solo borra con --borrar.

USO:  python borrar_ruido_silver_clientes.py            # simulacro
      python borrar_ruido_silver_clientes.py --borrar    # borra
"""
from __future__ import annotations
import os, sys, json, re, unicodedata

CONTENEDOR = "silver-clientes"
FUENTE = "noticias_cliente"

# Alias / nombres cortos por cliente (para no botar noticias válidas).
ALIAS = {
    "DANE": ["dane"],
    "POSITIVA": ["positiva"],
    "INFIHUILA": ["infihuila", "infi huila"],
    "CrediFlores": ["crediflores"],
    "Secretaria de Hacienda de Bogota": ["secretaria de hacienda", "hacienda bogota", "distrital de hacienda"],
    "Secretaria de Planeacion de Bogota": ["secretaria de planeacion", "planeacion bogota"],
    "Secretaria de Educacion de Bogota": ["secretaria de educacion", "educacion bogota"],
    "COLVATEL": ["colvatel"],
    "Informacion Gerencial Ltda": ["informacion gerencial"],
    "RED COMPUTO": ["red computo"],
    "RUNT": ["runt", "registro unico nacional de transito"],
    "COMWARE": ["comware"],
    "Famisanar EPS": ["famisanar"],
    "Loteria de Bogota": ["loteria de bogota"],
    "Ecopetrol": ["ecopetrol"],
    "Asobancaria": ["asobancaria"],
    "Claro": ["claro colombia", "claro telecomunicaciones"],  # estricto para evitar el "dejar claro"
    "Universidad Militar Nueva Granada": ["universidad militar", "nueva granada", "unimilitar"],
    "SOFT Management": ["soft management"],
}

_STOP = {"sa","sas","s","ltda","inc","corp","corporation","colombia","co","group",
         "grupo","de","del","la","el","los","las","y","the","consulting","consultores",
         "solutions","services","technology","tech","sistemas","latam","eps","compania"}


def _norm(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower())


def _nucleo(nombre: str) -> str:
    return " ".join(p for p in _norm(nombre).split() if p not in _STOP and len(p) > 2).strip()


def _es_valida(entidad: str, texto: str) -> bool:
    texto_norm = _norm(texto)
    palabras = set(texto_norm.split())
    nucleo = _nucleo(entidad)
    if nucleo and (nucleo in texto_norm or all(p in palabras for p in nucleo.split())):
        return True
    for alias in ALIAS.get(entidad, []):
        a = _norm(alias)
        if a and (a in texto_norm or all(p in palabras for p in a.split())):
            return True
    return False


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
    borrar = "--borrar" in sys.argv
    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(CONTENEDOR)

    ruidosas, validas, otros = [], 0, 0
    ej_borrar, ej_conservar = [], []

    print(f"Revisando '{CONTENEDOR}'...\n")
    for blob in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        if d.get("fuente_original") != FUENTE:
            otros += 1
            continue
        entidad = d.get("entidad_monitoreada") or d.get("entidad_declarada") or ""
        texto = " ".join([str(d.get("titulo") or ""), str(d.get("descripcion") or "")])
        titulo = (d.get("titulo") or "")[:60]
        if not _nucleo(entidad):
            validas += 1
            continue
        if _es_valida(entidad, texto):
            validas += 1
            if len(ej_conservar) < 8:
                ej_conservar.append((entidad, titulo))
        else:
            ruidosas.append(blob.name)
            if len(ej_borrar) < 15:
                ej_borrar.append((entidad, titulo))

    print("=" * 56)
    print("RESUMEN")
    print("=" * 56)
    print(f"  Noticias RUIDOSAS a borrar: {len(ruidosas)}")
    print(f"  Noticias VÁLIDAS (conservar): {validas}")
    print(f"  Otros blobs (no noticias):    {otros}")

    print("\n--- EJEMPLOS QUE BORRARÍA ---")
    for e, t in ej_borrar:
        print(f"  '{e}' -> {t}")
    print("\n--- EJEMPLOS QUE CONSERVA ---")
    for e, t in ej_conservar:
        print(f"  '{e}' -> {t}")

    if not borrar:
        print("\n[SIMULACRO] No borré nada. Revisa los ejemplos. Corre con --borrar si están bien.")
        return

    print(f"\nBorrando {len(ruidosas)} noticias ruidosas...")
    n = 0
    for nombre in ruidosas:
        try:
            cli.get_blob_client(nombre).delete_blob()
            n += 1
        except Exception as ex:
            print(f"  error: {nombre}: {ex}")
    print(f"Listo. Borré {n} ruidosas. Quedan {validas} noticias válidas.")


if __name__ == "__main__":
    main()