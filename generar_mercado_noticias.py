# -*- coding: utf-8 -*-
"""
generar_mercado_noticias.py  (v3 — con FECHA de cada noticia)
=============================================================
Extrae titulares relevantes de silver-mercado (pulso del sector) y
silver-proveedores (ecosistema), y los escribe en 'goldMercadoNoticias'.

v3: agrega la FECHA de cada noticia (de la fuente RSS/Google News), para
mostrarla en el tablero. Ordena las recientes primero.

USO:  python generar_mercado_noticias.py            # simulacro
      python generar_mercado_noticias.py --escribir  # escribe la tabla
"""
from __future__ import annotations
import os, sys, json, hashlib, re

FUENTES = [
    ("silver-mercado", "pulso"),
    ("silver-proveedores", "ecosistema"),
]
TABLA = "goldMercadoNoticias"

EVENTOS_UTILES = {"inversion", "alianza", "expansion", "lanzamiento", "regulatorio"}
MAX_POR_BLOQUE = 6

RUIDO = [
    "balvin", "james rodriguez", "james rodríguez", "shakira", "cantante",
    "futbolista", "futbol", "fútbol", "artista", "reggaeton", "reggaetón",
    "celebridad", "influencer", "farándula", "farandula",
]

RELEVANTE_TI = [
    "data center", "datacenter", "nube", "cloud", "software", "tecnolog",
    "digital", "aws", "azure", "oracle", "snowflake", "inteligencia artificial",
    "ciberseguridad", "startup", "saas", "plataforma", "inversión tecnológica",
    "transformación digital", "acuerdo", "alianza",
]


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


def _es_tema_ti(titulo: str) -> bool:
    """True si la noticia trata de tecnología / negocio digital / TI empresarial.
    Filtra noticias que tienen datos pero NO son del sector (petróleo, ciencia
    curiosa, deportes, etc.)."""
    t = titulo.lower()
    # excluir ciencia curiosa / no-negocio (aunque mencionen una empresa tech)
    no_negocio = [
        "mosca", "cerebro", "cientific", "neurona", "dinosaurio", "planeta",
        "espacio", "galaxia", "telescopio", "fosil", "fósil", "animal",
        "insecto", "especie", "universo", "asteroide", "meteorito", "volcan",
    ]
    if any(x in t for x in no_negocio):
        return False
    temas_ti = [
        "tecnolog", "digital", "software", "nube", "cloud", "datos", "data",
        "inteligencia artificial", " ia ", " ai ", "ciberseguridad", "seguridad informatica",
        "startup", "fintech", "saas", "plataforma", "app", "aplicaci",
        "innovaci", "transformaci", "internet", "telecom", "5g", "ecommerce",
        "comercio electr", "blockchain", "cripto", "chatbot", "copilot",
        "microsoft", "oracle", "aws", "amazon web", "google cloud", "azure",
        "ibm", "snowflake", "sap", "red hat", "nvidia", "intel", "huawei",
        "claro", "movistar", "tigo", "nequi", "nu ", "rappi", "mercado libre",
        "globant", "sistema", "automatiz", "algoritmo", "programaci",
        "centro de datos", "data center", "conectividad", "banda ancha",
        "licenciamiento", "servidor", "infraestructura tecnol",
    ]
    return any(x in t for x in temas_ti)


def _score_concrecion(titulo: str) -> int:
    """Puntúa qué tan CONCRETA es una noticia (más datos = más útil para IC).
    Prefiere títulos con montos, empresas, números — descarta los vagos."""
    import re
    t = titulo.lower()
    score = 0
    # montos concretos (lo más valioso)
    if re.search(r"\$|\bmillones?\b|\bbillones?\b|\bmil millones\b|d[oó]lares|usd", t):
        score += 3
    # nombres de empresas/entidades conocidas
    empresas = ["nu ", "claro", "microsoft", "oracle", "aws", "amazon", "google",
                "ibm", "movistar", "tigo", "bancolombia", "ecopetrol", "nequi",
                "mercado libre", "rappi", "globant", "snowflake", "sap", "huawei",
                "ntt", "davivienda", "aval", "nuam"]
    if any(e in t for e in empresas):
        score += 2
    # números concretos (rankings, cantidades, porcentajes)
    if re.search(r"\b\d+\b|%|ranking|puesto", t):
        score += 1
    # verbos de acción concreta (movimientos reales)
    acciones = ["invierte", "invertir", "anuncia", "lanza", "adquiere", "compra",
                "firma", "alianza", "expande", "abre", "construye", "presenta"]
    if any(a in t for a in acciones):
        score += 1
    # PENALIZAR frases vagas/opinión (sin datos concretos)
    vagas = ["no es suficiente", "pero", "podría", "debería", "hacia", "el futuro",
             "retos", "desafíos", "reflexión", "opinión", "análisis", "tendencia"]
    if any(v in t for v in vagas) and score < 3:
        score -= 2
    return score


def _es_ruido(titulo: str) -> bool:
    t = titulo.lower()
    if any(r in t for r in RUIDO):
        if not any(rel in t for rel in RELEVANTE_TI):
            return True
        if any(r in t for r in ["balvin", "james rodriguez", "james rodríguez", "shakira"]):
            return True
    return False


def main() -> None:
    from azure.storage.blob import BlobServiceClient
    escribir = "--escribir" in sys.argv
    bs = BlobServiceClient.from_connection_string(_conn())

    vistos = set()
    todas = []
    for contenedor, bloque in FUENTES:
        cli = bs.get_container_client(contenedor)
        candidatos = []
        for blob in cli.list_blobs():
            try:
                d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
            except Exception:
                continue
            titulo = (d.get("titulo") or "").strip()
            if not titulo or len(titulo) < 15:
                continue
            if _es_ruido(titulo):
                continue
            clave = re.sub(r"[^a-z0-9 ]", "", titulo.lower())[:50]
            if clave in vistos:
                continue
            clasif = d.get("clasificacion", {}) or {}
            tipo = clasif.get("tipo_senal", "")
            # excluir páginas de catálogo/producto (no son noticias reales)
            fuente_orig = str(d.get("fuente_original", "")).lower()
            if tipo == "referencia_web" or "web" in fuente_orig:
                continue
            # excluir títulos que son claramente catálogos/listados genéricos
            tl = titulo.lower()
            if any(x in tl for x in ["fabricantes -", "all red hat", "products",
                                      "soluciones tecnológicas |", "últimas noticias -",
                                      "solutions on aws", "- fedesoft", "- cenisoft",
                                      "empresas que us", "empresas del sector",
                                      "empresas innovadoras"]):
                continue
            # descartar noticias que NO son de TI/negocio digital (aunque tengan datos)
            if not _es_tema_ti(titulo):
                continue
            relev = (d.get("relevancia", {}) or {}).get("score", 0) or 0
            prioridad = 2 if tipo in EVENTOS_UTILES else (1 if relev >= 0.4 else 0)
            concrecion = _score_concrecion(titulo)  # qué tan concreta/útil es
            # --- fecha de la noticia + filtro de recencia (últimos 12 meses) ---
            fecha = d.get("fecha_evento_utc") or d.get("fecha_publicacion") or ""
            fecha_str = str(fecha)[:10] if fecha else ""
            if fecha_str:
                from datetime import datetime, timezone, timedelta
                corte = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d")
                if fecha_str < corte:
                    continue  # noticia de más de 1 año, se descarta
            candidatos.append({
                "bloque": bloque, "titulo": titulo[:160], "tipo": tipo,
                "fuente": d.get("origen_dato") or d.get("fuente_original", ""),
                "url": d.get("url", ""), "prioridad": prioridad, "relev": relev,
                "clave": clave, "fecha": str(fecha)[:10] if fecha else "",
                "concrecion": concrecion,
            })
        # ordenar: prioridad y relevancia primero (noticias buenas), luego fecha reciente
        candidatos.sort(key=lambda x: (x["concrecion"], x["prioridad"], x["fecha"] or "0000-00-00", x["relev"]), reverse=True)
        seleccion = candidatos[:MAX_POR_BLOQUE]
        for c in seleccion:
            vistos.add(c["clave"])
        print(f"\n{contenedor} ({bloque}): {len(seleccion)} titulares")
        for c in seleccion:
            print(f"   [{c.get('fecha','sin fecha') or 'sin fecha'}] [{c['tipo']}] {c['titulo'][:55]}")
        todas.extend(seleccion)

    filas = []
    for c in todas:
        rk = hashlib.sha256((c["bloque"] + c["titulo"]).encode("utf-8")).hexdigest()[:40]
        filas.append({
            "PartitionKey": c["bloque"], "RowKey": rk,
            "bloque": c["bloque"], "titulo": c["titulo"], "tipo": c["tipo"],
            "fuente": c["fuente"], "url": c["url"], "fecha": c["fecha"],
        })

    print(f"\nTotal titulares: {len(filas)}")
    if not escribir:
        print("\n[SIMULACRO] No escribí la tabla. Corre con --escribir para crearla.")
        return

    from azure.data.tables import TableServiceClient, UpdateMode
    import time as _t
    tsc = TableServiceClient.from_connection_string(_conn())
    # borrar la tabla vieja para no mezclar noticias viejas (sin fecha) con las nuevas
    try:
        tsc.delete_table(TABLA)
        print(f"  (tabla '{TABLA}' vieja borrada, recreando...)")
    except Exception:
        pass
    tabla = None
    for _i in range(12):
        try:
            tabla = tsc.create_table_if_not_exists(TABLA)
            break
        except Exception as ex:
            if "BeingDeleted" in str(ex) or "Conflict" in str(ex):
                print(f"     Azure libera la tabla; espero 15s ({_i+1}/12)...")
                _t.sleep(15)
            else:
                raise
    if tabla is None:
        print("La tabla sigue en borrado. Espera 1 min y reintenta."); return
    for f in filas:
        tabla.upsert_entity(f, mode=UpdateMode.REPLACE)
    print(f"Listo. Tabla '{TABLA}' con {len(filas)} titulares. Refresca Power BI.")


if __name__ == "__main__":
    main()