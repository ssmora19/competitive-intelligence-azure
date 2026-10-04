# -*- coding: utf-8 -*-
"""
limpiar_ruido_secop.py
======================
Borra SOLO los contratos de SECOP RUIDOSOS de bronze-competidores.

Un contrato es RUIDO si:
  - su competidor tiene 'secop_nombres' definido en companies.json, Y
  - el proveedor_adjudicado NO coincide con ninguno de esos nombres oficiales.

Deja INTACTOS:
  - los contratos reales (proveedor coincide con el nombre oficial),
  - los contratos de competidores SIN secop_nombres (no hay con qué juzgarlos),
  - todo lo que NO sea contrato SECOP (redes, noticias, etc.).

SEGURIDAD: por defecto es SIMULACRO (no borra). Solo borra con --borrar.

USO:  python limpiar_ruido_secop.py            # simulacro (muestra qué borraría)
      python limpiar_ruido_secop.py --borrar   # borra de verdad
"""

from __future__ import annotations
import os, sys, json, re, unicodedata

RUTA_COMPANIES = os.path.join("data", "companies.json")
CONTENEDOR = "bronze-competidores"


def _norm(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


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


def _cargar_secop_nombres() -> dict:
    """competidor(normalizado) -> lista de secop_nombres normalizados."""
    if not os.path.exists(RUTA_COMPANIES):
        print(f"No encontré {RUTA_COMPANIES}"); sys.exit(1)
    with open(RUTA_COMPANIES, encoding="utf-8") as f:
        companies = json.load(f)
    mapa = {}
    for c in companies:
        nombre = c.get("nombre", "")
        nombres = [_norm(n) for n in (c.get("secop_nombres") or []) if n]
        mapa[_norm(nombre)] = nombres
    return mapa


def _es_real(competidor: str, proveedor: str, mapa: dict) -> bool | None:
    """True=real, False=ruido, None=no se puede juzgar (sin secop_nombres)."""
    nombres = mapa.get(_norm(competidor))
    if not nombres:              # competidor sin secop_nombres -> no juzgar
        return None
    prov = _norm(proveedor)
    for nom in nombres:
        if nom and nom in prov:
            return True
    return False


def main() -> None:
    from azure.storage.blob import BlobServiceClient

    borrar = "--borrar" in sys.argv
    mapa = _cargar_secop_nombres()
    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(CONTENEDOR)

    total = reales = no_juzgables = ruido = 0
    a_borrar = []
    por_competidor = {}

    print(f"Revisando contratos SECOP en '{CONTENEDOR}'...\n")
    for blob in cli.list_blobs():
        # solo blobs de la fuente secop_competidor
        if not blob.name.startswith("secop_competidor/") and "secop" not in blob.name.lower():
            continue
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        raw = d.get("raw", {}) or {}
        meta = d.get("_meta", {}) or {}
        if "proveedor_adjudicado" not in raw and "referencia_del_contrato" not in raw:
            continue
        total += 1
        competidor = meta.get("empresa") or raw.get("_competidor_buscado") or ""
        proveedor = raw.get("proveedor_adjudicado") or ""
        veredicto = _es_real(competidor, proveedor, mapa)
        por_competidor.setdefault(competidor, [0, 0, 0])
        if veredicto is True:
            reales += 1; por_competidor[competidor][0] += 1
        elif veredicto is None:
            no_juzgables += 1; por_competidor[competidor][2] += 1
        else:
            ruido += 1; por_competidor[competidor][1] += 1
            a_borrar.append(blob.name)

    print("=" * 60)
    print("RESUMEN")
    print("=" * 60)
    print(f"  Total contratos SECOP:     {total}")
    print(f"  Reales (se conservan):     {reales}")
    print(f"  Sin secop_nombres (se dejan): {no_juzgables}")
    print(f"  RUIDO (se borrarían):      {ruido}")

    print("\nPor competidor (real / ruido / sin-juzgar):")
    for comp, (r, ru, nj) in sorted(por_competidor.items(), key=lambda x: -x[1][1]):
        print(f"  {comp[:28]:28} real={r:4} ruido={ru:4} sinjuzgar={nj:4}")

    if not borrar:
        print("\n[SIMULACRO] No borré nada. Corre con --borrar para borrar de verdad.")
        return

    print(f"\nBorrando {len(a_borrar)} blobs ruidosos...")
    for nombre in a_borrar:
        try:
            cli.get_blob_client(nombre).delete_blob()
        except Exception as ex:
            print(f"  error borrando {nombre}: {ex}")
    print(f"Listo. Borré {len(a_borrar)} contratos ruidosos.")


if __name__ == "__main__":
    main()