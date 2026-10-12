# -*- coding: utf-8 -*-
"""
validar_senales_silver.py
=========================
Para las entidades con veredicto importante, va a los contenedores silver-* y
muestra sus SEÑALES REALES (los títulos concretos de cada noticia/evento/contrato).
Sirve para verificar si las señales son hechos claros ("ganó contrato X") o ruido,
ANTES de decidir traerlas a Gold para las alertas.

Solo lee. No modifica nada.

USO:  python validar_senales_silver.py            # entidades de ejemplo
      python validar_senales_silver.py "Accenture" # una entidad puntual
"""
from __future__ import annotations
import os, sys, json, re, unicodedata

# Contenedores de Silver donde buscar señales
CONTENEDORES = [
    "silver-competidores", "silver-clientes", "silver-clientes-competencia",
    "silver-mercado", "silver-proveedores", "silver-tendencias",
    "silver-regulatorio", "silver-capacidades",
]

# Entidades de ejemplo (las que salieron en las alertas importantes)
EJEMPLO = ["Accenture", "INFIHUILA", "Universidad Militar", "BMIND"]
MAX_SENALES = 6


def _norm(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower())


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

    entidades = [sys.argv[1]] if len(sys.argv) > 1 else EJEMPLO

    for entidad in entidades:
        objetivo = _norm(entidad)
        print("\n" + "=" * 70)
        print(f"SEÑALES DE: {entidad}")
        print("=" * 70)
        encontradas = []
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
                # coincide si la entidad aparece en entidad_monitoreada/declarada o en el texto
                campos = " ".join([
                    str(d.get("entidad_monitoreada") or ""),
                    str(d.get("entidad_declarada") or ""),
                    str(d.get("titulo") or ""),
                ])
                if objetivo and objetivo in _norm(campos):
                    fecha = d.get("fecha_evento_utc") or ""
                    tipo = (d.get("clasificacion", {}) or {}).get("tipo_senal", "")
                    encontradas.append({
                        "titulo": (d.get("titulo") or "")[:90],
                        "fuente": d.get("fuente_original", ""),
                        "tipo": tipo,
                        "fecha": str(fecha)[:10],
                        "cont": cont.replace("silver-", ""),
                    })
        # ordenar por fecha (recientes primero), mostrar las top
        encontradas.sort(key=lambda x: x["fecha"], reverse=True)
        if not encontradas:
            print("  (no se encontraron señales para esta entidad)")
        for s in encontradas[:MAX_SENALES]:
            print(f"\n  [{s['cont']} · {s['tipo']} · {s['fecha']}]")
            print(f"  {s['titulo']}")
        print(f"\n  Total señales encontradas: {len(encontradas)}")


if __name__ == "__main__":
    main()