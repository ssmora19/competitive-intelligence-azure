# -*- coding: utf-8 -*-
"""
experimento_gemini.py
=====================
QUE HACE: clasifica los 80 registros del conjunto de validacion con Gemini
(tema, sentimiento, relevancia) y compara TRES fuentes: verdad humana vs reglas
vs Gemini. Guarda resultados parciales en CSV para no perder progreso.

PARA QUE SIRVE: experimento comparativo de la tesis. Numeros reales de si Gemini
supera a las reglas y alcanza los umbrales (temas 85%, sent 80%).

CORRECCIONES vs version anterior:
- Modelo con cuota amplia (flash-lite: ~1000/dia) en vez de 3.7-flash (20/dia).
- Reintentos con espera ante errores 503/429 temporales.
- Guardado parcial en resultados_gemini.csv (reanudable).

USO:
    python experimento_gemini.py
"""
import os
import csv
import json
import time
import sys

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# --- Config ---
# Modelos candidatos por orden de preferencia (cuota amplia primero).
# Verifica en AI Studio cual tiene free tier activo el dia que corras.
MODELOS_CANDIDATOS = [
    "gemini-2.5-flash-lite",   # 15 RPM / ~1000 RPD - ideal clasificacion
    "gemini-3.1-flash-lite",   # alternativa nueva
    "gemini-2.5-flash",        # 10 RPM / 250 RPD
]
ARCHIVO = "conjunto_validacion_final.csv"
SALIDA = "resultados_gemini.csv"
PAUSA_SEG = 5              # ~12/min, por debajo de 15 RPM
MAX_REINTENTOS = 4        # ante 503/429 temporales

TEMAS = ["lanzamiento", "inversion", "alianza", "regulatorio", "expansion",
         "contratacion_publica", "demanda_talento", "contenido_corporativo",
         "contenido_audiovisual", "actividad_tecnica", "noticia", "referencia_web"]
SENTIMIENTOS = ["positivo", "neutro", "negativo"]


def get_api_key():
    k = os.environ.get("GEMINI_API_KEY", "")
    if k:
        return k
    with open("local.settings.json", encoding="utf-8") as f:
        return json.load(f).get("Values", {}).get("GEMINI_API_KEY", "")


PROMPT = """Eres un analista de inteligencia competitiva para una empresa de TI en Colombia.
Clasifica el siguiente contenido (noticia o publicacion en redes) devolviendo SOLO un JSON valido, sin texto adicional.

Contenido:
Titulo: {titulo}
Descripcion: {descripcion}

Devuelve un JSON con exactamente estas tres claves:
- "tema": uno de {temas}
- "sentimiento": uno de {sentimientos}
- "relevante": "si" o "no". Es relevante si menciona a un actor del sector TI/negocio (cliente, competidor o empresa tecnologica) Y describe un evento de negocio (contrato, alianza, lanzamiento, inversion, expansion, regulatorio, contratacion de talento). Resultados financieros generales, farandula, deportes, sucesos policiales o menciones de pasada NO son relevantes.

Responde SOLO el JSON."""


def elegir_modelo(client):
    """Prueba los modelos candidatos con una llamada minima; usa el primero que responda."""
    for m in MODELOS_CANDIDATOS:
        try:
            client.models.generate_content(
                model=m, contents="ok",
                config=types.GenerateContentConfig(max_output_tokens=5),
            )
            print(f"Modelo activo: {m}")
            return m
        except Exception as e:
            print(f"  {m} no disponible ({str(e)[:60]}...)")
    print("Ningun modelo candidato respondio. Revisa tu cuota o los IDs.")
    sys.exit(1)


def clasificar(client, modelo, titulo, descripcion):
    prompt = PROMPT.format(
        titulo=titulo[:300], descripcion=descripcion[:800],
        temas=TEMAS, sentimientos=SENTIMIENTOS,
    )
    for intento in range(MAX_REINTENTOS):
        try:
            resp = client.models.generate_content(
                model=modelo, contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0, response_mime_type="application/json",
                ),
            )
            data = json.loads(resp.text)
            return (
                str(data.get("tema", "")).strip().lower(),
                str(data.get("sentimiento", "")).strip().lower(),
                str(data.get("relevante", "")).strip().lower(),
            )
        except genai_errors.APIError as e:
            code = getattr(e, "code", None)
            if code in (429, 503) and intento < MAX_REINTENTOS - 1:
                espera = 15 * (intento + 1)
                print(f"    ({code}; reintento en {espera}s)")
                time.sleep(espera)
                continue
            print(f"    (error {code}: {str(e)[:70]})")
            return None, None, None
        except Exception as e:
            print(f"    (error: {str(e)[:70]})")
            return None, None, None
    return None, None, None


def main():
    api_key = get_api_key()
    if not api_key:
        print("ERROR: no se encontro GEMINI_API_KEY")
        sys.exit(1)
    client = genai.Client(api_key=api_key)
    modelo = elegir_modelo(client)

    with open(ARCHIVO, encoding="utf-8-sig") as f:
        filas = list(csv.DictReader(f))

    # Reanudar: cargar lo ya clasificado
    ya = {}
    if os.path.exists(SALIDA):
        with open(SALIDA, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                ya[r["id"]] = r
        print(f"Reanudando: {len(ya)} registros ya clasificados.\n")

    resultados = []
    for i, r in enumerate(filas, 1):
        rid = r["id"]
        if rid in ya and ya[rid].get("g_tema"):
            resultados.append(ya[rid]); continue

        g_tema, g_sent, g_rel = clasificar(client, modelo, r["titulo"], r["descripcion"])
        fila = {
            "id": rid, "familia": r["familia"], "titulo": r["titulo"][:60],
            "tema_real": r["tema_real"], "sent_real": r["sent_real"],
            "rel_real": r["relevante_real"],
            "tema_reglas": r["tema_predicho"], "sent_reglas": r["sent_predicho"],
            "g_tema": g_tema or "", "g_sent": g_sent or "", "g_rel": g_rel or "",
        }
        resultados.append(fila)

        # Guardado parcial cada vuelta
        with open(SALIDA, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(fila.keys()))
            w.writeheader(); w.writerows(resultados)

        print(f"[{i}/{len(filas)}] {r['titulo'][:42]:42} | real={r['tema_real'][:12]:12} gemini={str(g_tema)[:12]}")
        time.sleep(PAUSA_SEG)

    # --- Reporte ---
    total = sum(1 for r in resultados if r["tema_real"].strip())
    def acc(campo_pred, campo_real):
        ok = sum(1 for r in resultados
                 if r[campo_real].strip() and r[campo_pred].strip().lower() == r[campo_real].strip().lower())
        return f"{100*ok/total:.1f}%" if total else "s/d"

    print("\n" + "="*60)
    print(f"RESULTADOS: REGLAS vs GEMINI (contra verdad humana, n={total})")
    print("="*60)
    print(f"{'Metrica':<16}{'Reglas':<12}{'Gemini':<12}{'Umbral'}")
    print("-"*52)
    print(f"{'Tema':<16}{acc('tema_reglas','tema_real'):<12}{acc('g_tema','tema_real'):<12}85%")
    print(f"{'Sentimiento':<16}{acc('sent_reglas','sent_real'):<12}{acc('g_sent','sent_real'):<12}80%")
    print(f"{'Relevancia':<16}{'(aparte)':<12}{acc('g_rel','rel_real'):<12}-")
    fallos = sum(1 for r in resultados if not r["g_tema"].strip())
    if fallos:
        print(f"\n(Gemini fallo/pendiente en {fallos} registros)")
    print(f"\nResultados guardados en {SALIDA}")


if __name__ == "__main__":
    main()