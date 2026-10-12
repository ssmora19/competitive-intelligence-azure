# -*- coding: utf-8 -*-
"""
resumir_veredictos.py  (v3 — porqué por DETALLE de noticia)
==========================================================
Genera un resumen ACCIONABLE por cada veredicto, y ahora incluye el PORQUÉ REAL:
por cada entidad lee sus noticias limpias de Silver y se las pasa a Groq, que
elige la MÁS RELEVANTE para el negocio (ignora bolsa, astronomía, ruido) y la
usa como sustento de la alerta.

Si la entidad no tiene noticias relevantes, Groq se basa en el tipo/score
(porqué por actividad) sin inventar hechos.

REANUDABLE: salta los que ya tienen resumen_ia.

USO:  python resumir_veredictos.py            # todos los pendientes
      python resumir_veredictos.py 5          # tope de 5 (para PROBAR)
"""
from __future__ import annotations
import os, sys, time, json, re, unicodedata

TABLA = "goldVeredictos"
MODELO_GROQ = "qwen/qwen3.8-27b"
PAUSA_SEG = 2.1
CLAVE_GROQ_EN_SETTINGS = "GROQ_API_KEY"
MIN_SCORE = 0.0

CONTENEDORES_NOTICIA = ["silver-competidores", "silver-clientes", "silver-clientes-competencia"]
FUENTES_NOTICIA = {"noticias_competidor", "noticias_cliente", "noticias_competencia"}
MAX_NOTICIAS = 5   # noticias por entidad que se le pasan a Groq

GUIA_TIPO = {
    "nivel_amenaza":          ("amenaza competitiva", "cómo defenderse o reaccionar"),
    "lead_score":             ("lead comercial", "por qué contactarlo y con qué prioridad"),
    "oportunidad_captura":    ("cliente de la competencia capturable", "cómo abordarlo para ganarlo"),
    "oportunidad_mercado":    ("oportunidad de mercado", "cómo aprovecharla"),
    "alerta_cambio":          ("cambio en un proveedor", "qué revisar o ajustar"),
    "alerta_regulatoria":     ("cambio regulatorio", "qué atender para cumplir o aprovechar"),
    "gap_capacidad":          ("brecha de capacidad de TAK", "qué capacidad desarrollar"),
    "relevancia_tecnologica": ("tendencia tecnológica", "qué vigilar o adoptar"),
}

_cache_noticias = None


def _norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower())


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING."); sys.exit(1)


def _groq_key():
    env = os.environ.get(CLAVE_GROQ_EN_SETTINGS)
    if env: return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get(CLAVE_GROQ_EN_SETTINGS):
                return v[CLAVE_GROQ_EN_SETTINGS]
    print(f"No encontré '{CLAVE_GROQ_EN_SETTINGS}'."); sys.exit(1)


def _cargar_noticias():
    """Carga todas las noticias de Silver en memoria: entidad_norm -> [titulos]."""
    global _cache_noticias
    if _cache_noticias is not None:
        return _cache_noticias
    from azure.storage.blob import BlobServiceClient
    bs = BlobServiceClient.from_connection_string(_conn())
    idx = {}
    for cont in CONTENEDORES_NOTICIA:
        try:
            cli = bs.get_container_client(cont)
            blobs = list(cli.list_blobs())
        except Exception:
            continue
        for b in blobs:
            try:
                d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
            except Exception:
                continue
            if d.get("fuente_original") not in FUENTES_NOTICIA:
                continue
            ent_raw = d.get("entidad_monitoreada") or d.get("entidad_declarada") or ""
            ent = _norm(re.sub(r"\(cliente de.*?\)", "", ent_raw, flags=re.IGNORECASE))
            titulo = (d.get("titulo") or "").strip()
            fecha = str(d.get("fecha_evento_utc") or "")[:10]
            if ent and titulo:
                idx.setdefault(ent, []).append((fecha, titulo))
    # ordenar cada lista por fecha desc
    for ent in idx:
        idx[ent].sort(key=lambda x: x[0], reverse=True)
    _cache_noticias = idx
    print(f"  (cargadas noticias de {len(idx)} entidades)")
    return idx


def _noticias_de(nombre):
    idx = _cargar_noticias()
    obj = _norm(nombre)
    if not obj:
        return []
    # match por contención
    for ent, lst in idx.items():
        if obj in ent or ent in obj:
            return [t for _, t in lst[:MAX_NOTICIAS]]
    return []


SYSTEM = (
    "Eres analista de inteligencia competitiva de TAK (empresa de TI). Escribe UNA "
    "alerta ACCIONABLE para el gerente, en español, máximo 26 palabras. Estructura: "
    "el HECHO relevante (qué pasó) + la ACCIÓN recomendada. "
    "Te doy noticias recientes de la entidad: ELIGE la MÁS relevante para el negocio "
    "de TI y úsala como el hecho. IGNORA noticias de bolsa/acciones/dividendos, "
    "astronomía, deportes o temas ajenos al negocio de TI. "
    "MUY IMPORTANTE: si ninguna noticia es relevante, NO menciones que no hay noticias "
    "ni digas frases como 'ninguna noticia es relevante' o 'sin noticias'. En ese caso, "
    "escribe SOLO la acción recomendada según el tipo y la prioridad, de forma natural "
    "y directa (ej. 'Lead de alta prioridad: contactar para explorar oportunidades de X'). "
    "Nunca inventes hechos que no estén en las noticias. No uses comillas."
)


def _prompt(ent, noticias):
    tipo = ent.get("tipo_veredicto", "")
    desc, accion = GUIA_TIPO.get(tipo, (tipo, "qué acción tomar"))
    try:
        score = f"{float(ent.get('score', 0)):.2f}"
    except (TypeError, ValueError):
        score = "n/d"
    bloque_noticias = "\n".join(f"  - {t}" for t in noticias) if noticias else "  (sin noticias relevantes)"
    return (
        f"Entidad: {ent.get('entidad_nombre','')}\n"
        f"Tipo de veredicto: {desc}\n"
        f"Score (0 a 1): {score}\n"
        f"La acción debe orientarse a: {accion}.\n\n"
        f"Noticias recientes de la entidad:\n{bloque_noticias}\n\n"
        f"Escribe la alerta accionable (hecho relevante + acción)."
    )


def main():
    from azure.data.tables import TableServiceClient, UpdateMode
    from groq import Groq

    tope = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    cliente = Groq(api_key=_groq_key())
    tsc = TableServiceClient.from_connection_string(_conn())
    tabla = tsc.get_table_client(TABLA)

    print("Cargando noticias de Silver...")
    _cargar_noticias()

    entidades = list(tabla.list_entities())
    print(f"Veredictos: {len(entidades)}")
    hechos = saltados = errores = 0
    for ent in entidades:
        if tope is not None and hechos >= tope:
            break
        if ent.get("resumen_ia"):
            saltados += 1; continue
        nombre = (ent.get("entidad_nombre") or "").strip()
        if not nombre:
            continue
        try:
            if float(ent.get("score", 0)) < MIN_SCORE:
                continue
        except (TypeError, ValueError):
            pass
        noticias = _noticias_de(nombre)
        try:
            resp = cliente.chat.completions.create(
                model=MODELO_GROQ,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": _prompt(ent, noticias)}],
                temperature=0.4, max_tokens=110,
            )
            texto = resp.choices[0].message.content.strip().strip('"')
        except Exception as ex:
            errores += 1; print(f"  ERROR {nombre}: {ex}"); continue
        ent["resumen_ia"] = texto
        tabla.update_entity(ent, mode=UpdateMode.MERGE)
        hechos += 1
        print(f"  [{hechos}] {nombre[:20]:20} ({len(noticias)}n) -> {texto}")
        time.sleep(PAUSA_SEG)

    print(f"\ngenerados={hechos}  ya_tenian={saltados}  errores={errores}")


if __name__ == "__main__":
    main()