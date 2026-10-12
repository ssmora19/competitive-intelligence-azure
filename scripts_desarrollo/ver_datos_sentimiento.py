# -*- coding: utf-8 -*-
"""
ver_datos_sentimiento.py
========================
QUÉ HACE: mide si hay datos suficientes de SENTIMIENTO para usarlo en el scoring
de captura (noticias negativas sobre un competidor -> sus clientes son más
capturables). Solo lee. Uso local (no se despliega: .funcignore ver_*).

Responde 3 preguntas:
  1. ¿Cuántas noticias de competidores tienen sentimiento calculado (y por qué método)?
  2. ¿Cuántas negativas recientes hay por competidor prioritario?
  3. ¿Las negativas son de verdad negativas sobre el competidor? (muestra los titulares)

USO:  python ver_datos_sentimiento.py               (pantalla)
      python ver_datos_sentimiento.py sentimiento.txt   (además guarda archivo UTF-8)
"""
import os
import sys
import json
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

from azure.storage.blob import BlobServiceClient

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

COMPETIDORES = ["seti", "it crowd", "comware", "iteria", "bmind",
                "global technology services", "grow data"]
salida = []


def p(t=""):
    print(t)
    salida.append(t)


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    return json.load(open("local.settings.json", encoding="utf-8"))["Values"]["AZURE_STORAGE_CONNECTION_STRING"]


def _norm(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(t.split())


def _competidor(d):
    texto = _norm(f"{d.get('entidad_declarada', '')} {d.get('entidad_monitoreada', '')}")
    for c in COMPETIDORES:
        if c in texto:
            return c
    return None


hoy = datetime.now(timezone.utc)
lim90 = (hoy - timedelta(days=90)).strftime("%Y-%m-%d")
lim180 = (hoy - timedelta(days=180)).strftime("%Y-%m-%d")

cont = BlobServiceClient.from_connection_string(_conn()).get_container_client("silver-competidores")
total = noticias = relevantes = 0
con_sent = Counter()          # método -> cantidad
etiquetas = Counter()
por_comp = defaultdict(lambda: Counter())
negativas = defaultdict(list)

print("Leyendo silver-competidores (puede tardar unos minutos)...")
for b in cont.list_blobs():
    total += 1
    try:
        d = json.loads(cont.get_blob_client(b.name).download_blob().readall())
    except Exception:
        continue
    fuente = _norm(d.get("fuente_original", ""))
    if "secop" in fuente:
        continue                                  # contratos: no tienen "tono"
    noticias += 1
    if not (d.get("relevancia") or {}).get("pasa_umbral", False):
        continue
    relevantes += 1
    comp = _competidor(d)
    s = d.get("sentimiento") or {}
    etq = _norm(s.get("etiqueta", "")) if isinstance(s, dict) else _norm(s)
    metodo = (s.get("metodo", "sin_metodo") if isinstance(s, dict) else "texto") if etq else "sin_sentimiento"
    con_sent[metodo] += 1
    if etq:
        etiquetas[etq] += 1
    if not comp:
        continue
    fecha = str(d.get("fecha_evento_utc") or "")[:10]
    por_comp[comp]["relevantes"] += 1
    if etq:
        por_comp[comp]["con_sentimiento"] += 1
    if etq.startswith("negativ"):
        por_comp[comp]["negativas"] += 1
        if fecha >= lim180:
            por_comp[comp]["neg_180d"] += 1
        if fecha >= lim90:
            por_comp[comp]["neg_90d"] += 1
        negativas[comp].append((fecha, d.get("titulo", "")[:110], d.get("url", "")))

p("=" * 78)
p("1. COBERTURA DEL SENTIMIENTO (noticias de competidores, sin SECOP)")
p("=" * 78)
p(f"Blobs en silver-competidores: {total}  |  noticias: {noticias}  |  relevantes: {relevantes}")
for m, n in con_sent.most_common():
    p(f"   {m:20} {n:6}  ({n / max(relevantes, 1):.0%} de las relevantes)")
p(f"Etiquetas encontradas: {dict(etiquetas)}")

p("\n" + "=" * 78)
p("2. NOTICIAS POR COMPETIDOR PRIORITARIO")
p("=" * 78)
p(f"{'competidor':28} {'relevantes':>10} {'con sent.':>10} {'negativas':>10} {'neg 180d':>9} {'neg 90d':>8}")
for c in COMPETIDORES:
    x = por_comp.get(c, Counter())
    p(f"{c:28} {x['relevantes']:>10} {x['con_sentimiento']:>10} {x['negativas']:>10} {x['neg_180d']:>9} {x['neg_90d']:>8}")

p("\n" + "=" * 78)
p("3. MUESTRA DE NEGATIVAS (¿son de verdad negativas sobre el competidor?)")
p("=" * 78)
for c in COMPETIDORES:
    lst = sorted(negativas.get(c, []), reverse=True)[:5]
    if not lst:
        continue
    p(f"\n{c.upper()}")
    for fecha, tit, url in lst:
        p(f"   [{fecha or 'sin fecha'}] {tit}")

# vínculo cliente de la competencia -> competidor
try:
    cc = json.load(open(os.path.join("ingestion_clientes_competencia", "clientes_competencia.json"), encoding="utf-8"))
    vinc = Counter(_norm(x.get("competidor", "")) for x in cc if x.get("activo", True))
    p("\n" + "=" * 78)
    p("4. CLIENTES DE LA COMPETENCIA POR COMPETIDOR (a quiénes beneficiaría la señal)")
    p("=" * 78)
    for c, n in vinc.most_common():
        p(f"   {c:28} {n} clientes")
except Exception as ex:
    p(f"\n(No se pudo leer clientes_competencia.json: {ex})")

if len(sys.argv) > 1:
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        f.write("\n".join(salida))
    print(f"\nGuardado en '{sys.argv[1]}'.")