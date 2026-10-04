# -*- coding: utf-8 -*-
"""
extraer_cifras_mercado.py  (v2 — con FUENTE y FECHA de cada cifra)
==================================================================
Lee noticias de silver-mercado y silver-proveedores, y usa Groq para extraer
hasta 3 CIFRAS CLAVE del mercado TI colombiano. NOVEDAD v2: cada cifra guarda
el TITULAR fuente y su FECHA, para poder sustentar de dónde salió (defendible
ante el jurado/cliente).

USO:  python extraer_cifras_mercado.py            # muestra lo extraído (no escribe)
      python extraer_cifras_mercado.py --escribir  # escribe la tabla
"""
from __future__ import annotations
import os, sys, json, re, unicodedata
from datetime import datetime, timezone

FUENTES = ["silver-mercado", "silver-proveedores"]
TABLA = "goldCifrasMercado"
MODELO_GROQ = "qwen/qwen3.8-27b"
CLAVE_GROQ_EN_SETTINGS = "GROQ_API_KEY"

RESPALDO = [
    {"valor": "$61 billones", "etiqueta": "mueve al año la industria del software en Colombia", "fuente": "", "fecha": ""},
    {"valor": "85.000", "etiqueta": "profesionales digitales que le faltan al país", "fuente": "", "fecha": ""},
    {"valor": "70%", "etiqueta": "de los clientes de TAK son del sector público", "fuente": "", "fecha": ""},
]

SYSTEM = (
    "Eres analista de mercado. De los titulares numerados que te doy, extrae "
    "entre 2 y 3 cifras que describan el CONTEXTO MACRO del mercado de tecnología "
    "en Colombia — el tamaño del sector, no movimientos de una sola empresa. "
    "Busca cifras cuantitativas claras (en dinero, porcentaje o cantidad) de temas como: "
    "tamaño del mercado/industria TI, déficit o demanda de talento digital, "
    "crecimiento o adopción tecnológica del sector. "
    "REGLAS: cada cifra de un tema distinto; solo cifras EXPLÍCITAS en los titulares; "
    "NO inversiones de una empresa individual (Nu, Claro...); "
    "NO frecuencias vagas ('cada 5 minutos') ni cifras sin unidad clara. "
    "Es MEJOR devolver 2 cifras sólidas que forzar una 3ra floja. "
    "Devuelve SOLO un JSON válido (array de 2 o 3 objetos): "
    '[{"valor":"$61 billones","etiqueta":"tamaño del mercado de software","id_titular":3}, ...]. '
    "'valor' = la cifra tal como aparece; 'etiqueta' = frase corta (máx 10 palabras); "
    "'id_titular' = el NÚMERO del titular fuente."
)


def _norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _conn() -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING."); sys.exit(1)


def _groq_key() -> str:
    env = os.environ.get(CLAVE_GROQ_EN_SETTINGS)
    if env: return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get(CLAVE_GROQ_EN_SETTINGS):
                return v[CLAVE_GROQ_EN_SETTINGS]
    print(f"No encontré la clave '{CLAVE_GROQ_EN_SETTINGS}'."); sys.exit(1)


def main() -> None:
    from azure.storage.blob import BlobServiceClient
    from azure.data.tables import TableServiceClient, UpdateMode
    from groq import Groq
    escribir = "--escribir" in sys.argv

    bs = BlobServiceClient.from_connection_string(_conn())
    # guardar titular + fecha + fuente de cada noticia
    noticias = []
    for cont in FUENTES:
        cli = bs.get_container_client(cont)
        for blob in cli.list_blobs():
            try:
                d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
            except Exception:
                continue
            t = (d.get("titulo") or "").strip()
            de = (d.get("descripcion") or "").strip()
            fecha = str(d.get("fecha_evento_utc") or "")[:10]
            if t:
                noticias.append({
                    "texto": f"{t}. {de}"[:220],
                    "titulo": t,
                    "fecha": fecha,
                })

    # priorizar las que tienen números Y son recientes
    con_num = [n for n in noticias if any(c.isdigit() for c in n["texto"])]
    con_num.sort(key=lambda x: x["fecha"], reverse=True)  # recientes primero
    muestra = (con_num or noticias)[:60]  # muestra amplia para encontrar cifras macro

    # numerar los titulares para que la IA diga de cuál sacó la cifra
    bloque = "\n".join(f"[{i}] {n['texto']}" for i, n in enumerate(muestra))
    prompt = f"TITULARES NUMERADOS:\n{bloque}\n\nExtrae las 3 cifras clave en JSON (con id_titular)."

    cifras = None
    try:
        cliente = Groq(api_key=_groq_key())
        resp = cliente.chat.completions.create(
            model=MODELO_GROQ,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=350,
        )
        txt = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        try:
            cifras = json.loads(txt)
        except Exception:
            # parseo robusto: extraer solo el array [...] aunque venga con texto extra
            m = re.search(r"\[.*\]", txt, re.DOTALL)
            if m:
                cifras = json.loads(m.group(0))
            else:
                cifras = None
        if not isinstance(cifras, list) or not cifras:
            cifras = None
    except Exception as ex:
        print(f"  (no se pudo extraer con IA: {ex})")

    if not cifras:
        print("  Usando cifras de RESPALDO.")
        cifras = RESPALDO
    else:
        # enriquecer cada cifra con la fuente y fecha del titular que indicó la IA
        for c in cifras:
            idt = c.get("id_titular")
            if isinstance(idt, int) and 0 <= idt < len(muestra):
                c["fuente"] = muestra[idt]["titulo"]
                c["fecha"] = muestra[idt]["fecha"]
            else:
                c["fuente"] = ""
                c["fecha"] = ""

    cifras = cifras[:3]  # máximo 3, pero acepta 2
    print("\n=== CIFRAS EXTRAÍDAS ===")
    for c in cifras:
        print(f"   {c.get('valor','?')}  ->  {c.get('etiqueta','?')}")
        print(f"      fuente: {(c.get('fuente','') or 'sin fuente')[:55]}  [{c.get('fecha','') or 'sin fecha'}]")

    if not escribir:
        print("\n[SIMULACRO] No escribí la tabla. Corre con --escribir para crearla.")
        return

    tsc = TableServiceClient.from_connection_string(_conn())
    tabla = tsc.create_table_if_not_exists(TABLA)
    for i, c in enumerate(cifras):
        tabla.upsert_entity({
            "PartitionKey": "cifra",
            "RowKey": str(i),
            "orden": i,
            "valor": str(c.get("valor", "")),
            "etiqueta": str(c.get("etiqueta", "")),
            "fuente": str(c.get("fuente", "")),
            "fecha": str(c.get("fecha", "")),
        }, mode=UpdateMode.REPLACE)
    print(f"\nListo. Tabla '{TABLA}' con {len(cifras)} cifras (con fuente y fecha). Refresca Power BI.")


if __name__ == "__main__":
    main()