# -*- coding: utf-8 -*-
"""
prueba_minima.py
================

QUÉ HACE
--------
Prueba mínima del clasificador de eventos. Toma unas pocas noticias reales de tu
Silver (marcadas 'noticia' que pasan el umbral de relevancia) y las clasifica en
tus etiquetas de evento usando el motor configurado (Groq por defecto). Imprime
el resultado y cuánto tardó. NO escribe nada.

PARA QUÉ SIRVE
--------------
Ver, con bajo riesgo y en segundos: (1) que el motor funciona, (2) cuánto tarda
por noticia, (3) si clasifica bien sobre TUS datos reales — antes de la validación
completa o la pasada de producción.

REQUISITOS
    pip install groq              (si MOTOR = 'groq')
    + la API key de Groq en local.settings.json (bloque "Values", clave GROQ_API_KEY)

CÓMO SE EJECUTA (desde ingestion_silver/clasificacion_eventos/)
    python prueba_minima.py
    python prueba_minima.py silver-clientes-competencia 8
"""

from __future__ import annotations

import os
import sys
import json
import time

import config_eventos as cfg
import clasificador


def _texto_de_noticia(d: dict) -> str:
    """QUÉ HACE: arma el texto a clasificar (título + descripción), recortado."""
    titulo = d.get("titulo", "") or ""
    desc = d.get("descripcion", "") or ""
    return f"{titulo}. {desc}".strip()[:cfg.MAX_CHARS_TEXTO]


def _connection_string() -> str:
    """QUÉ HACE: lee la cadena de conexión de Azure (entorno o local.settings.json)."""
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    for ruta in ("local.settings.json", os.path.join("..", "local.settings.json"),
                 os.path.join("..", "..", "local.settings.json"),
                 os.path.join("..", "..", "..", "local.settings.json")):
        if os.path.exists(ruta):
            with open(ruta, "r", encoding="utf-8") as f:
                vals = json.load(f).get("Values", {})
            if vals.get("AZURE_STORAGE_CONNECTION_STRING"):
                return vals["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING (ni en local.settings.json).")
    sys.exit(1)


def cargar_noticias(contenedor: str, cuantas: int) -> list[dict]:
    """QUÉ HACE: lee unas pocas noticias relevantes reales de tu Blob Storage."""
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        print("Falta azure-storage-blob. Instala:  pip install azure-storage-blob")
        sys.exit(1)
    svc = BlobServiceClient.from_connection_string(_connection_string())
    cli = svc.get_container_client(contenedor)
    noticias = []
    # contadores de diagnóstico: para entender qué hay realmente en el contenedor
    total, con_tipo_noticia, relevantes_noticia = 0, 0, 0
    tipos_relevantes = {}
    for blob in cli.list_blobs():
        if len(noticias) >= cuantas:
            break
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        total += 1
        clasif = d.get("clasificacion", {}) or {}
        relev = d.get("relevancia", {}) or {}
        tipo = clasif.get("tipo_senal")
        pasa = relev.get("pasa_umbral") is True
        if tipo == cfg.TIPO_ORIGEN:
            con_tipo_noticia += 1
        if pasa:
            tipos_relevantes[tipo] = tipos_relevantes.get(tipo, 0) + 1
        es_relevante = pasa or not cfg.SOLO_RELEVANTES
        if tipo == cfg.TIPO_ORIGEN and es_relevante:
            noticias.append(d)
            relevantes_noticia += 1
    if not noticias:
        print(f"   [diagnóstico] revisé {total} blobs: {con_tipo_noticia} son 'noticia', "
              f"pero 0 son 'noticia' Y relevantes.")
        print(f"   [diagnóstico] tipos que SÍ pasan el umbral aquí: {tipos_relevantes}")
    return noticias


def main() -> None:
    contenedor = sys.argv[1] if len(sys.argv) > 1 else "silver-tendencias"
    cuantas = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 6

    print(f"\n1) Preparando el motor '{cfg.MOTOR}'...")
    t0 = time.time()
    motor = clasificador.obtener_motor()
    print(f"   Motor listo en {time.time() - t0:.1f} s.\n")

    print(f"2) Leyendo {cuantas} noticias relevantes de '{contenedor}'...")
    noticias = cargar_noticias(contenedor, cuantas)
    if not noticias:
        print("   No encontré noticias relevantes en ese contenedor. Prueba otro.")
        sys.exit(0)
    print(f"   {len(noticias)} noticias cargadas.\n")

    print("3) Clasificando...\n" + "=" * 70)
    t0 = time.time()
    for i, d in enumerate(noticias, 1):
        texto = _texto_de_noticia(d)
        etiqueta, conf = motor.clasificar(texto)
        original = (d.get("clasificacion", {}) or {}).get("tipo_senal", "?")
        print(f"\n[{i}] {d.get('titulo', '(sin título)')[:75]}")
        print(f"    Silver decía: '{original}'")
        print(f"    Modelo dice:  '{etiqueta}'  (confianza {conf:.2f})")
        # pausa para respetar el límite del tier gratuito de Groq
        if cfg.MOTOR == "groq":
            time.sleep(cfg.GROQ_PAUSA_SEG)

    total = time.time() - t0
    print("\n" + "=" * 70)
    print(f"\nListo. {len(noticias)} noticias en {total:.1f} s "
          f"(~{total / len(noticias):.1f} s por noticia, incluida la pausa anti-límite).")
    print("\nSi las clasificaciones tienen sentido, pasamos a validacion.py")
    print("(medir precisión sobre tus 80 etiquetados vs reglas vs Gemini).\n")


if __name__ == "__main__":
    main()