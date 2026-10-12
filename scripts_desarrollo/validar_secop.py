# -*- coding: utf-8 -*-
"""
validar_secop.py  (v2 — comparación por núcleo distintivo del nombre)
====================================================================
Revisa TODOS los contratos de SECOP en un contenedor de Bronze y valida si el
competidor buscado aparece de verdad en el proveedor adjudicado.

v2: en vez de exigir el nombre COMPLETO, compara el NÚCLEO distintivo del nombre
(quitando palabras genéricas como 'corporation', 'sas', 'colombia', 'group'...).
Así 'IBM Corporation' SÍ coincide con 'IBM de Colombia SAS'.

SOLO CUENTA Y REPORTA. No borra ni modifica nada.

USO:  python validar_secop.py            # bronze-competidores
      python validar_secop.py <contenedor>
"""

from __future__ import annotations
import os
import sys
import json
import re
import unicodedata

# Palabras genéricas que NO identifican a una empresa (no sirven para comparar).
_STOP = {
    "sa", "sas", "s", "a", "ltda", "inc", "corp", "corporation", "colombia", "co",
    "group", "grupo", "de", "del", "la", "el", "los", "las", "y", "the",
    "compania", "nacional", "consulting", "consultores", "solutions", "services",
    "group", "technology", "tech", "sistemas", "ltd", "llc", "pwc",
}


def _norm(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _nucleo(nombre: str) -> str:
    """Devuelve el núcleo distintivo: el nombre sin palabras genéricas."""
    palabras = [p for p in _norm(nombre).split() if p not in _STOP and len(p) > 1]
    return " ".join(palabras).strip()


def _coincide(competidor: str, proveedor: str) -> bool:
    """True si el núcleo del competidor aparece en el proveedor normalizado."""
    nuc = _nucleo(competidor)
    prov = _norm(proveedor)
    if not nuc or not prov:
        return False
    # El núcleo completo aparece como frase en el proveedor.
    if nuc in prov:
        return True
    # O todas las palabras del núcleo aparecen en el proveedor (orden distinto).
    return all(p in prov.split() for p in nuc.split())


def _conn() -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json"),
              os.path.join("..", "..", "local.settings.json"),
              os.path.join("..", "..", "..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING."); sys.exit(1)


def main() -> None:
    from azure.storage.blob import BlobServiceClient

    contenedor = sys.argv[1] if len(sys.argv) > 1 else "bronze-competidores"
    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(contenedor)

    total = reales = ruido = 0
    por_competidor = {}
    ejemplos_ruido = []

    print(f"Revisando contratos SECOP en '{contenedor}' (v2)...\n")
    for blob in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        meta = d.get("_meta", {}) or {}
        raw = d.get("raw", {}) or {}
        if "proveedor_adjudicado" not in raw and "referencia_del_contrato" not in raw:
            continue
        total += 1
        competidor = meta.get("empresa") or raw.get("_competidor_buscado") or ""
        proveedor = raw.get("proveedor_adjudicado") or ""
        por_competidor.setdefault(competidor, [0, 0])
        if _coincide(competidor, proveedor):
            reales += 1
            por_competidor[competidor][0] += 1
        else:
            ruido += 1
            por_competidor[competidor][1] += 1
            if len(ejemplos_ruido) < 20:
                ejemplos_ruido.append((competidor, proveedor, (raw.get("objeto_del_contrato") or "")[:55]))

    print("=" * 64)
    print("RESUMEN (v2)")
    print("=" * 64)
    print(f"  Total contratos SECOP:  {total}")
    print(f"  Reales:                 {reales}")
    print(f"  Ruido:                  {ruido}")
    if total:
        print(f"  % de ruido:             {100*ruido/total:.0f}%")

    print("\n" + "=" * 64)
    print("POR COMPETIDOR (reales / ruido)")
    print("=" * 64)
    for comp, (re_, ru_) in sorted(por_competidor.items(), key=lambda x: -x[1][1]):
        print(f"  {comp[:30]:30} reales={re_:4}  ruido={ru_:4}")

    print("\n" + "=" * 64)
    print("EJEMPLOS DE RUIDO (competidor -> proveedor)")
    print("=" * 64)
    for comp, prov, obj in ejemplos_ruido:
        print(f"  '{comp}' -> '{prov[:33]}'  | {obj}")


if __name__ == "__main__":
    main()