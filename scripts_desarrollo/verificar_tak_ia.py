# -*- coding: utf-8 -*-
"""
verificar_tak_ia.py
===================
Lista TODOS los contratos de TAK en goldContratos y muestra a qué tecnología
corresponde cada uno, para verificar con precisión si TAK tiene o no contratos
de IA (u otra tecnología) en el sector PÚBLICO (SECOP).

Solo lee. No modifica nada.

USO:  python verificar_tak_ia.py
"""
from __future__ import annotations
import os, sys, json, re

TABLA_CONTRATOS = "goldContratos"

CATEGORIAS = {
    "IA / Machine Learning": [r"\bmachine learning\b", r"\bdeep learning\b", r"\bllm\b",
        r"\binteligencia artificial\b", r"\bchatbot\b", r"\banal[ií]tic", r"\bpredic"],
    "Cloud / Nube": [r"\baws\b", r"\bazure\b", r"\bcloud\b", r"\bnube\b", r"\bpaas\b", r"\bdrp\b", r"\bbackup\b"],
    "Ciberseguridad": [r"\bseguridad\b", r"\bfirewall\b", r"\bvulnerab"],
    "Datos / BI": [r"\boracle\b", r"\bexadata\b", r"\bbase de datos\b", r"\blicenc", r"\bdata integrator\b"],
    "Infraestructura": [r"\bservidor", r"\bdell\b", r"\bhardware\b", r"\binfraestructura\b"],
}


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


def _clasificar(texto: str):
    t = (texto or "").lower()
    cats = []
    for cat, patrones in CATEGORIAS.items():
        for p in patrones:
            if re.search(p, t):
                cats.append(cat)
                break
    return cats or ["(sin clasificar)"]


def main() -> None:
    from azure.data.tables import TableServiceClient
    tsc = TableServiceClient.from_connection_string(_conn())
    tc = tsc.get_table_client(TABLA_CONTRATOS)

    taks = [e for e in tc.list_entities() if e.get("es_tak") == "Sí"]
    print(f"Contratos de TAK en SECOP (goldContratos): {len(taks)}\n")
    print("=" * 70)
    for e in taks:
        obj = e.get("objeto", "")
        cats = _clasificar(obj)
        print(f"\n  Entidad: {e.get('entidad_contratante','?')}")
        print(f"  Tecnología: {', '.join(cats)}")
        print(f"  Objeto: {obj[:100]}")


if __name__ == "__main__":
    main()