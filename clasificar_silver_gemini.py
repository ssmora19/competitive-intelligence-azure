# -*- coding: utf-8 -*-
"""
clasificar_silver_gemini.py
===========================
QUE HACE: recorre los registros de la capa Silver, toma SOLO los relevantes
(los que pasaron el filtro de relevancia por reglas), los envia a Gemini para
que reclasifique el tema, y guarda el resultado de vuelta en cada
registro Silver. Trabaja en TANDAS respetando el limite diario de Gemini y es
REANUDABLE: lleva un registro de lo ya procesado para no repetir ni gastar cuota.

PARA QUE SIRVE: prepara la capa Silver para alimentar Gold. Aplica la decision
del experimento: Gemini para el tema (mejor en lenguaje/idiomas),
reglas para relevancia (mejor en criterio de negocio). Filtrar por relevancia
ANTES de llamar a Gemini ahorra la mayor parte de la cuota (no se gasta en ruido).

ESTRATEGIA DE CUOTA:
- Solo se envian a Gemini los registros con relevancia.pasa_umbral == True.
- LIMITE_DIARIO controla cuantas llamadas hace por corrida (default conservador).
- Se marca cada registro con "clasificado_por": "gemini" para no repetir.
- Si se corta, se vuelve a correr y continua donde quedo.

USO:
    python clasificar_silver_gemini.py                 # procesa hasta LIMITE_DIARIO
    python clasificar_silver_gemini.py --limite 500    # personaliza el tope
    python clasificar_silver_gemini.py --contenedor silver-competidores  # uno solo
    python clasificar_silver_gemini.py --dry-run       # solo cuenta, no llama a Gemini
"""
import os
import sys
import csv
import json
import time
import argparse

from azure.storage.blob import BlobServiceClient
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# --- Configuracion ---
CONTENEDORES = [
    "silver-competidores", "silver-clientes", "silver-clientes-competencia",
    "silver-tendencias", "silver-proveedores", "silver-regulatorio",
    "silver-mercado", "silver-capacidades",
]
MODELOS_CANDIDATOS = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash"]
LIMITE_DIARIO_DEFAULT = 450     # margen bajo el 500/dia real del free tier
PAUSA_SEG = 5                   # ~12/min, bajo el limite de 15 RPM
MAX_REINTENTOS = 4
PROGRESO_CSV = "progreso_gemini.csv"   # ids ya clasificados (atajo local para reanudar)
# En Azure el disco de la app es de solo lectura: el CSV se guarda en la carpeta
# temporal. La fuente de verdad es la marca "clasificado_por" dentro de cada blob.
if not os.access(".", os.W_OK):
    import tempfile
    PROGRESO_CSV = os.path.join(tempfile.gettempdir(), "progreso_gemini.csv")

TEMAS = ["lanzamiento", "inversion", "alianza", "regulatorio", "expansion",
         "contratacion_publica", "demanda_talento", "contenido_corporativo",
         "contenido_audiovisual", "actividad_tecnica", "noticia", "referencia_web"]

PROMPT = """Eres un analista de inteligencia competitiva para una empresa de TI en Colombia.
Clasifica el siguiente contenido devolviendo SOLO un JSON valido, sin texto adicional.

Titulo: {titulo}
Descripcion: {descripcion}

Devuelve JSON con exactamente una clave:

- "tema": uno de {temas}

Responde SOLO el JSON."""
# NOTA: el sentimiento se retiró del sistema (oct-2026). Al medirlo en las noticias de
# competidores, el 98,5 % era neutro o positivo y las pocas negativas no correspondían
# a hechos adversos de los competidores: no aportaba señal para ninguna decisión.


def get_api_key():
    k = os.environ.get("GEMINI_API_KEY", "")
    if k:
        return k
    with open("local.settings.json", encoding="utf-8") as f:
        return json.load(f).get("Values", {}).get("GEMINI_API_KEY", "")


def get_conn():
    # Cuenta de DATOS (stinteligencia), igual que Bronze y Gold.
    c = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    if c:
        return c
    with open("local.settings.json", encoding="utf-8") as f:
        return json.load(f).get("Values", {}).get("AZURE_STORAGE_CONNECTION_STRING", "")


def elegir_modelo(client):
    for m in MODELOS_CANDIDATOS:
        try:
            client.models.generate_content(
                model=m, contents="ok",
                config=types.GenerateContentConfig(max_output_tokens=5))
            print(f"Modelo activo: {m}")
            return m
        except Exception as e:
            print(f"  {m} no disponible ({str(e)[:50]}...)")
    print("Ningun modelo candidato respondio. Revisa cuota/IDs.")
    sys.exit(1)


def clasificar(client, modelo, titulo, descripcion):
    prompt = PROMPT.format(titulo=(titulo or "")[:300], descripcion=(descripcion or "")[:800],
                           temas=TEMAS)
    for intento in range(MAX_REINTENTOS):
        try:
            resp = client.models.generate_content(
                model=modelo, contents=prompt,
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"))
            data = json.loads(resp.text)
            return str(data.get("tema", "")).strip().lower()
        except genai_errors.APIError as e:
            code = getattr(e, "code", None)
            if code in (429, 503) and intento < MAX_REINTENTOS - 1:
                espera = 15 * (intento + 1)
                print(f"      ({code}; reintento en {espera}s)")
                time.sleep(espera)
                continue
            # 429 al final = cuota agotada: señal para parar la corrida
            if code == 429:
                return "__CUOTA__"
            print(f"      (error {code})")
            return None
        except Exception as e:
            print(f"      (error: {str(e)[:50]})")
            return None
    return None


def cargar_progreso():
    hechos = set()
    if os.path.exists(PROGRESO_CSV):
        with open(PROGRESO_CSV, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                hechos.add(r["blob"])
    return hechos


def anexar_progreso(blob_id):
    try:
        nuevo = not os.path.exists(PROGRESO_CSV)
        with open(PROGRESO_CSV, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if nuevo:
                w.writerow(["blob"])
            w.writerow([blob_id])
    except OSError:
        pass   # sin disco escribible: basta la marca clasificado_por del blob


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=LIMITE_DIARIO_DEFAULT)
    ap.add_argument("--contenedor", type=str, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conts = [args.contenedor] if args.contenedor else CONTENEDORES

    # Timeout de conexion para no colgarse indefinidamente en un blob.
    bs = BlobServiceClient.from_connection_string(get_conn(), connection_timeout=20, read_timeout=40)

    hechos = cargar_progreso()
    print(f"Ya clasificados en corridas previas: {len(hechos)}")

    def descargar_blob(cc, nombre, reintentos=3):
        """Descarga un blob con reintentos; devuelve el dict o None si falla."""
        for intento in range(reintentos):
            try:
                data = cc.get_blob_client(nombre).download_blob(max_concurrency=1).readall()
                return json.loads(data)
            except Exception:
                if intento < reintentos - 1:
                    time.sleep(2)
                    continue
                return None
        return None

    # --- FASE 1: inventario de relevantes pendientes ---
    pendientes = []   # (contenedor, blob_name)
    total_rel = 0
    for cont in conts:
        cc = bs.get_container_client(cont)
        try:
            blobs = list(cc.list_blobs())
        except Exception as e:
            print(f"  (no se pudo listar {cont}: {str(e)[:50]})")
            continue
        print(f"  inventariando {cont} ({len(blobs)} blobs)...")
        for b in blobs:
            bid = f"{cont}/{b.name}"
            if bid in hechos:
                continue   # ya clasificado: no hace falta descargarlo
            reg = descargar_blob(cc, b.name)
            if reg is None:
                continue
            if reg.get("clasificado_por") == "gemini":
                continue   # ya clasificado (marca dentro del blob)
            if not reg.get("relevancia", {}).get("pasa_umbral", False):
                continue   # ruido: no gastar Gemini
            total_rel += 1
            pendientes.append((cont, b.name))

    print(f"Registros relevantes pendientes por clasificar: {len(pendientes)}")

    if args.dry_run:
        print("\n(dry-run) No se llamo a Gemini. Solo inventario.")
        if pendientes:
            print(f"Con limite {args.limite}/dia, necesitarias ~{-(-len(pendientes)//args.limite)} dias.")
        return

    if not pendientes:
        print("\nNada pendiente. Toda la Silver relevante ya esta clasificada por Gemini.")
        return

    # --- FASE 2: clasificar en tandas ---
    api_key = get_api_key()
    if not api_key:
        print("ERROR: falta GEMINI_API_KEY"); sys.exit(1)
    client = genai.Client(api_key=api_key)
    modelo = elegir_modelo(client)

    procesados = 0
    tanda = pendientes[:args.limite]
    print(f"\nProcesando {len(tanda)} registros (limite {args.limite})...\n")

    for i, (cont, blob_name) in enumerate(tanda, 1):
        cc = bs.get_container_client(cont)
        bc = cc.get_blob_client(blob_name)
        try:
            reg = json.loads(bc.download_blob().readall())
        except Exception:
            continue

        g_tema = clasificar(client, modelo, reg.get("titulo"), reg.get("descripcion"))

        if g_tema == "__CUOTA__":
            print(f"\n*** CUOTA DIARIA AGOTADA en el registro {i}. ***")
            print(f"Progreso guardado. Vuelve a correr manana (reset 7PM Colombia) para continuar.")
            break
        if not g_tema:
            continue   # error puntual: se reintentara en otra corrida (no se marca hecho)

        # Actualiza los campos con la clasificacion de Gemini, preservando lo demas.
        reg.setdefault("clasificacion", {})
        reg["clasificacion"]["tipo_senal"] = g_tema
        reg["clasificacion"]["metodo"] = "gemini"
        reg["clasificado_por"] = "gemini"

        try:
            bc.upload_blob(json.dumps(reg, ensure_ascii=False).encode("utf-8"), overwrite=True)
        except Exception as e:
            print(f"      (no se pudo guardar {blob_name}: {str(e)[:40]})")
            continue

        anexar_progreso(f"{cont}/{blob_name}")
        procesados += 1
        print(f"[{i}/{len(tanda)}] {(reg.get('titulo') or '')[:42]:42} -> {g_tema}")
        time.sleep(PAUSA_SEG)

    print(f"\n== Corrida terminada. Clasificados: {procesados} ==")
    restantes = len(pendientes) - procesados
    if restantes > 0:
        print(f"Quedan {restantes} pendientes. Vuelve a correr para continuar la siguiente tanda.")
    else:
        print("Toda la Silver relevante quedo clasificada por Gemini. Lista para Gold.")


if __name__ == "__main__":
    main()