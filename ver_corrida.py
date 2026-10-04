# -*- coding: utf-8 -*-
"""
ver_corrida.py
==============
QUÉ HACE: muestra, paso por paso, lo que imprimió cada script en una corrida
del orquestador (tabla pipelineLog de stinteligencia).

PARA QUÉ SIRVE: revisar que cada paso hizo su trabajo de verdad, no solo que
terminó sin caerse. Uso local; no se despliega (.funcignore: ver_*).

USO:
    python ver_corrida.py                                     # última corrida
    python ver_corrida.py 20261004-042020                     # una corrida específica
    python ver_corrida.py 20261004-042020 40                  # 40 líneas por paso
    python ver_corrida.py 20261004-042020 40 corrida.txt      # además lo guarda en un
                                                              # archivo UTF-8 (tildes y emojis bien)
"""
import os
import sys
import json

# La consola de Windows no soporta emojis/tildes de todos los scripts: se reemplazan
# en pantalla en vez de detener el programa. El archivo (si se pide) va en UTF-8 completo.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from azure.data.tables import TableServiceClient


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    v = json.load(open("local.settings.json", encoding="utf-8")).get("Values", {})
    return v["AZURE_STORAGE_CONNECTION_STRING"]


tsc = TableServiceClient.from_connection_string(_conn())
corrida = sys.argv[1] if len(sys.argv) > 1 else None
lineas = int(sys.argv[2]) if len(sys.argv) > 2 else 15
archivo = sys.argv[3] if len(sys.argv) > 3 else None
salida = []


def mostrar(texto=""):
    print(texto)
    salida.append(texto)


if not corrida:
    corrida = tsc.get_table_client("pipelineEstado").get_entity("pipeline", "actual")["corrida"]

filas = sorted(tsc.get_table_client("pipelineLog").query_entities(f"PartitionKey eq '{corrida}'"),
               key=lambda e: e["RowKey"])
mostrar(f"Corrida {corrida}: {len(filas)} pasos registrados\n")
for e in filas:
    mostrar("=" * 78)
    mostrar(f"{'OK   ' if e['ok'] else 'FALLO'} {e['paso']}  ({e['segundos']} s, {e['fecha']})")
    mostrar("-" * 78)
    texto = [l for l in (e.get("detalle") or "").splitlines() if l.strip()]
    for l in texto[-lineas:]:
        largo = 700 if ("Rate limit" in l or "Error" in l) else 160   # errores completos
        mostrar("   " + l[:largo])
    mostrar()

if archivo:
    with open(archivo, "w", encoding="utf-8") as f:
        f.write("\n".join(salida))
    print(f"\nGuardado en '{archivo}'.")