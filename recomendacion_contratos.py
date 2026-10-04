# -*- coding: utf-8 -*-
"""
recomendacion_contratos.py  (v2 — 9 competidores prioritarios)
==============================================================
Genera UNA recomendación estratégica (con IA/Groq) a partir de los datos de
contratos SECOP de la tabla goldContratos: qué gana TAK, qué ganan los
competidores, y la brecha. La guarda en 'goldRecomendacionContratos'.

v2: actualizada la lista de competidores prioritarios a los 9 (incluye
Accenture, IBM, Global Technology Services y Grow Data). Antes solo contaba 5,
lo que daba una brecha distinta a la gráfica del tablero.

USO:  python recomendacion_contratos.py
"""
from __future__ import annotations
import os, sys, json
from datetime import datetime, timezone

TABLA_CONTRATOS = "goldContratos"
TABLA_RESUMEN = "goldRecomendacionContratos"
MODELO_GROQ = "qwen/qwen3.8-27b"
CLAVE_GROQ_EN_SETTINGS = "GROQ_API_KEY"

# Los 9 competidores prioritarios (mismos que goldContratos y el filtro de Gold)
PRIORITARIOS = {"SETI", "IT CROWD", "Comware", "Iteria", "BMIND",
                "Global Technology Services", "Grow Data"}

SYSTEM = (
    "Eres analista jefe de inteligencia competitiva de TAK, una empresa "
    "colombiana de servicios de TI especializada en Oracle y nube. Escribe una "
    "RECOMENDACIÓN ESTRATÉGICA para el gerente: máximo 3 frases, en español, "
    "clara y accionable. Combina el panorama (lo que ganan los competidores vs "
    "lo que gana TAK en contratación PÚBLICA) y cierra con UNA acción concreta y "
    "priorizada. Básate solo en los datos dados. No inventes cifras ni nombres. "
    "No uses comillas. Aclara que es sector público cuando sea relevante."
)


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


def _groq_key() -> str:
    env = os.environ.get(CLAVE_GROQ_EN_SETTINGS)
    if env:
        return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json"),
              os.path.join("..", "..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get(CLAVE_GROQ_EN_SETTINGS):
                return v[CLAVE_GROQ_EN_SETTINGS]
    print(f"No encontré la clave '{CLAVE_GROQ_EN_SETTINGS}'."); sys.exit(1)


def main() -> None:
    from azure.data.tables import TableServiceClient, UpdateMode
    from groq import Groq

    tsc = TableServiceClient.from_connection_string(_conn())
    tc = tsc.get_table_client(TABLA_CONTRATOS)

    filas = list(tc.list_entities())
    if not filas:
        print("La tabla goldContratos está vacía."); return

    tak_total = 0.0; tak_ents = {}
    comp_total = 0.0; comp_por = {}
    for f in filas:
        try:
            valor = float(f.get("valor") or 0)
        except (TypeError, ValueError):
            valor = 0
        if f.get("es_tak") == "Sí":
            tak_total += valor
            ent = f.get("entidad_contratante", "?")
            tak_ents[ent] = tak_ents.get(ent, 0) + valor
        else:
            comp = f.get("competidor", "?")
            if comp in PRIORITARIOS:
                comp_total += valor
                comp_por[comp] = comp_por.get(comp, 0) + valor

    top_comp = sorted(comp_por.items(), key=lambda x: -x[1])[:5]
    top_tak = sorted(tak_ents.items(), key=lambda x: -x[1])[:5]
    mult = comp_total / tak_total if tak_total else 0

    contexto = (
        f"CONTRATACIÓN PÚBLICA (SECOP):\n"
        f"- Total ganado por los competidores prioritarios: ${comp_total:,.0f}\n"
        f"- Total ganado por TAK: ${tak_total:,.0f}\n"
        f"- Los competidores ganan {mult:.0f} veces más que TAK.\n\n"
        f"Lo que más gana cada competidor:\n"
        + "\n".join(f"  - {c}: ${v:,.0f}" for c, v in top_comp) +
        f"\n\nEntidades donde TAK ya gana:\n"
        + "\n".join(f"  - {e}: ${v:,.0f}" for e, v in top_tak) +
        f"\n\nTAK se especializa en Oracle y servicios de nube.\n\n"
        f"Escribe la recomendación estratégica."
    )

    cliente = Groq(api_key=_groq_key())
    resp = cliente.chat.completions.create(
        model=MODELO_GROQ,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": contexto}],
        temperature=0.4,
        max_tokens=200,
    )
    texto = resp.choices[0].message.content.strip().strip('"')
    print("\n=== RECOMENDACIÓN GENERADA ===\n")
    print(texto)
    print()
    print(f"(Brecha usada: {mult:.0f}x | Competidores: ${comp_total:,.0f} | TAK: ${tak_total:,.0f})")

    tr = tsc.create_table_if_not_exists(TABLA_RESUMEN)
    tr.upsert_entity({
        "PartitionKey": "recomendacion",
        "RowKey": "contratos",
        "texto": texto,
        "generado_ts_utc": datetime.now(timezone.utc).isoformat(),
    }, mode=UpdateMode.REPLACE)
    print(f"Guardado en '{TABLA_RESUMEN}'. Refresca Power BI.")


if __name__ == "__main__":
    main()