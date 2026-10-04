# -*- coding: utf-8 -*-
"""
recomendacion_mercado.py
========================
Genera UNA recomendación estratégica de MERCADO (con Groq) a partir de la tabla
goldTecnologias: qué demanda el mercado, dónde TAK domina y dónde tiene huecos
sin explotar. La guarda en 'goldRecomendacionMercado' para el tablero.

Honestidad: distingue sector público (contratos SECOP) de la capacidad general.

USO:  python recomendacion_mercado.py
"""
from __future__ import annotations
import os, sys, json
from datetime import datetime, timezone

TABLA_TEC = "goldTecnologias"
TABLA_RESUMEN = "goldRecomendacionMercado"
MODELO_GROQ = "qwen/qwen3.8-27b"
CLAVE_GROQ_EN_SETTINGS = "GROQ_API_KEY"

SYSTEM = (
    "Eres analista jefe de inteligencia de mercado de TAK, empresa colombiana de "
    "servicios de TI (Oracle, nube, datos, IA, ciberseguridad). Escribe UNA "
    "recomendación estratégica de mercado para el gerente: máximo 3 frases, en "
    "español, clara y accionable. Cruza qué demanda el mercado con dónde TAK ya "
    "domina (tiene contratos) y dónde tiene capacidad pero no ha capturado el "
    "mercado público (sin explotar). Cierra con UNA acción concreta priorizada. "
    "Contexto del sector: el mercado de software en Colombia mueve ~61 billones "
    "de pesos al año y hay déficit de ~85.000 profesionales digitales. Básate "
    "solo en los datos dados. No inventes cifras extra. No uses comillas."
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
    tt = tsc.get_table_client(TABLA_TEC)

    lineas = []
    for e in tt.list_entities():
        try:
            dem = int(e.get("demanda_n") or 0)
        except (TypeError, ValueError):
            dem = 0
        lineas.append(
            f"- {e.get('categoria','?')}: demanda {e.get('demanda','?')} "
            f"({dem} menciones), estado '{e.get('estado','?')}', "
            f"{e.get('contratos_tak',0)} contratos públicos de TAK"
        )

    contexto = "SITUACIÓN DE MERCADO POR TECNOLOGÍA:\n" + "\n".join(lineas) + \
               "\n\nEscribe la recomendación estratégica de mercado."

    cliente = Groq(api_key=_groq_key())
    resp = cliente.chat.completions.create(
        model=MODELO_GROQ,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": contexto}],
        temperature=0.4,
        max_tokens=200,
    )
    texto = resp.choices[0].message.content.strip().strip('"')
    print("\n=== RECOMENDACIÓN DE MERCADO GENERADA ===\n")
    print(texto)
    print()

    tr = tsc.create_table_if_not_exists(TABLA_RESUMEN)
    tr.upsert_entity({
        "PartitionKey": "recomendacion",
        "RowKey": "mercado",
        "texto": texto,
        "generado_ts_utc": datetime.now(timezone.utc).isoformat(),
    }, mode=UpdateMode.REPLACE)
    print(f"Guardado en '{TABLA_RESUMEN}'. Refresca Power BI.")


if __name__ == "__main__":
    main()