# -*- coding: utf-8 -*-
"""
resumen_ejecutivo.py
====================
Genera UNA lectura ejecutiva del trimestre (con recomendación) a partir de los
veredictos importantes de Gold, usando Groq, y la guarda en la tabla nueva
'goldResumenEjecutivo' para que Power BI la muestre en la franja de cierre.

Distinto a resumir_veredictos.py:
  - Aquel genera UNA frase POR veredicto (79 textos).
  - Este genera UN SOLO texto que sintetiza TODO el panorama.

Honestidad: el prompt habla del ESTADO ACTUAL (foto del trimestre), no de
tendencias, porque los veredictos no tienen historia temporal.

CÓMO SE EJECUTA (desde donde ve tu local.settings.json)
    python resumen_ejecutivo.py
"""

from __future__ import annotations
import os, sys, json
from datetime import datetime, timezone

# ── CONFIG (igual que resumir_veredictos.py) ──
TABLA_VEREDICTOS = "goldVeredictos"
TABLA_RESUMEN    = "goldResumenEjecutivo"
MODELO_GROQ = "qwen/qwen3.8-27b"
CLAVE_GROQ_EN_SETTINGS = "GROQ_API_KEY"
MIN_SCORE = 0.60   # solo veredictos importantes

DESC_TIPO = {
    "nivel_amenaza": "amenaza competitiva",
    "lead_score": "lead comercial",
    "oportunidad_captura": "oportunidad de captura de cliente de la competencia",
    "oportunidad_mercado": "oportunidad de mercado",
    "alerta_regulatoria": "alerta regulatoria",
    "alerta_cambio": "cambio en proveedor",
    "gap_capacidad": "brecha de capacidad",
    "relevancia_tecnologica": "tendencia tecnológica",
}

SYSTEM = (
    "Eres analista jefe de inteligencia competitiva de una empresa de TI. "
    "Escribe una LECTURA EJECUTIVA para el gerente: máximo 3 frases, en español, "
    "clara y orientada a la acción. Resume el panorama del trimestre actual y "
    "cierra con UNA recomendación concreta. Habla del estado ACTUAL, no de "
    "tendencias ni de evolución en el tiempo. Básate solo en los datos dados. "
    "No inventes cifras ni nombres que no aparezcan. No uses comillas."
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
    tv = tsc.get_table_client(TABLA_VEREDICTOS)

    # 1) Recolectar los veredictos importantes (con entidad)
    conteo = {}
    lineas = []
    for e in tv.list_entities():
        try:
            score = float(e.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        if score < MIN_SCORE:
            continue
        nombre = (e.get("entidad_nombre") or "").strip()
        if not nombre:
            continue
        tipo = e.get("tipo_veredicto", "")
        desc = DESC_TIPO.get(tipo, tipo)
        conteo[desc] = conteo.get(desc, 0) + 1
        lineas.append(f"- {nombre}: {desc} (score {score:.2f})")

    if not lineas:
        print("No hay veredictos importantes para resumir."); return

    resumen_conteo = "; ".join(f"{n} {d}(s)" for d, n in conteo.items())
    contexto = (
        f"Resumen de conteos: {resumen_conteo}.\n\n"
        f"Detalle de los veredictos más relevantes del trimestre:\n"
        + "\n".join(lineas[:40])
    )

    # 2) Pedir la lectura a Groq
    cliente = Groq(api_key=_groq_key())
    resp = cliente.chat.completions.create(
        model=MODELO_GROQ,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": contexto}],
        temperature=0.4,
        max_tokens=180,
    )
    texto = resp.choices[0].message.content.strip().strip('"')
    print("\n=== LECTURA EJECUTIVA GENERADA ===\n")
    print(texto)
    print()

    # 3) Guardar en la tabla nueva (una sola fila, se sobrescribe cada vez)
    tr = tsc.create_table_if_not_exists(TABLA_RESUMEN)
    entidad = {
        "PartitionKey": "resumen",
        "RowKey": "actual",
        "texto": texto,
        "generado_ts_utc": datetime.now(timezone.utc).isoformat(),
    }
    tr.upsert_entity(entidad, mode=UpdateMode.REPLACE)
    print(f"Guardado en la tabla '{TABLA_RESUMEN}'. Refresca Power BI.")


if __name__ == "__main__":
    main()