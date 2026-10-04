# -*- coding: utf-8 -*-
"""
clasificar_eventos.py
=====================

QUÉ HACE
--------
Batch de producción: recorre las noticias RELEVANTES con evento concreto de un
contenedor de Silver, las reclasifica con el modelo, y ACTUALIZA el blob:
  - reemplaza clasificacion.tipo_senal por el del modelo (Gold lo lee directo)
  - guarda el valor original en clasificacion.tipo_senal_reglas (trazabilidad)
  - deja metadatos del modelo usado y la fecha

PARA QUÉ SIRVE
--------------
Corregir la clasificación de eventos que alimenta Gold. Se validó que el modelo
sube la exactitud de 51% a 71% y reduce los falsos eventos de 37% a 4%. Este
script aplica esa corrección a todo el corpus relevante.

DECISIONES DE DISEÑO (defendibles)
----------------------------------
- REANUDABLE: si un blob ya tiene 'tipo_senal_reglas', ya fue procesado y se
  salta. Se puede parar (Ctrl+C) y retomar sin reprocesar — igual que las tandas
  de Gemini en Silver. Clave por el límite del tier gratuito.
- TRAZABILIDAD: nunca se pierde el valor de las reglas; queda en tipo_senal_reglas.
- SOLO RELEVANTES CON EVENTO: no gasta llamadas en ruido ni en registros sin evento.
- RESPETA EL LÍMITE: pausa entre llamadas (config_eventos.GROQ_PAUSA_SEG).
- TOPE POR CORRIDA (--limite N): Groq gratis da 200.000 tokens al día (ventana
  móvil de 24 h) y el tablero también los necesita. El orquestador corre con
  --limite 150 (~68.000 tokens); lo pendiente avanza en las corridas siguientes.
- SI GROQ DICE "BASTA" (error 429): se detiene ordenadamente, sin caerse; lo ya
  clasificado queda guardado y lo pendiente sigue en la próxima corrida.

CÓMO SE EJECUTA (desde ingestion_silver/clasificacion_eventos/)
    python clasificar_eventos.py silver-competidores        # un contenedor
    python clasificar_eventos.py silver-competidores 50     # tope de 50 (prueba)
    python clasificar_eventos.py --todos                    # todos los contenedores
    python clasificar_eventos.py --todos --limite 150       # todos, máx. 150 por corrida

RECOMENDACIÓN
    Empieza por UN contenedor con un tope pequeño para verificar que actualiza
    bien los blobs, antes de soltar todo.
"""

from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timezone

import config_eventos as cfg
import clasificador

# Tipos de evento que se reclasifican (los que alimentan Gold). El ruido genérico
# 'noticia' se dejó fuera: se validó que aporta poco rescatarlo.
# 'contratacion_publica' TAMBIÉN se excluye: viene estructurado y correctamente
# tipificado desde SECOP (la fuente ya dice que es un contrato), así que las reglas
# no se equivocan ahí. El modelo aporta valor en las fuentes de TEXTO LIBRE
# (prensa, redes), donde la clasificación por reglas mostraba ~37% de falsos.
EVENTOS = {"alianza", "lanzamiento", "expansion", "inversion",
           "regulatorio", "actividad_tecnica"}

# Se conservan tal cual, sin pasar por el modelo (ya están bien desde la fuente).
EXCLUIDOS_YA_CORRECTOS = {"contratacion_publica"}

CONTENEDORES = [
    "silver-competidores", "silver-clientes", "silver-clientes-competencia",
    "silver-tendencias", "silver-proveedores", "silver-mercado",
    "silver-regulatorio", "silver-capacidades",
]


def _conn() -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json"),
              os.path.join("..", "..", "local.settings.json"),
              os.path.join("..", "..", "..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré la cadena de Azure."); sys.exit(1)


def _texto(d: dict) -> str:
    t = (d.get("titulo", "") or "")
    de = (d.get("descripcion", "") or "")
    return f"{t}. {de}".strip()[:cfg.MAX_CHARS_TEXTO]


def procesar_contenedor(blob_svc, contenedor: str, tope: int | None, motor) -> dict:
    import time
    cli = blob_svc.get_container_client(contenedor)

    procesados = cambiados = saltados_ya = saltados_norel = saltados_secop = 0
    limite_groq = False
    print(f"\n{'='*60}\n{contenedor}\n{'='*60}")
    for blob in cli.list_blobs():
        if tope is not None and procesados >= tope:
            break
        blob_cli = cli.get_blob_client(blob.name)
        try:
            d = json.loads(blob_cli.download_blob().readall())
        except Exception:
            continue

        clasif = d.get("clasificacion", {}) or {}
        relev = d.get("relevancia", {}) or {}
        tipo = clasif.get("tipo_senal")

        # ya procesado antes → saltar (reanudable)
        if "tipo_senal_reglas" in clasif:
            saltados_ya += 1
            continue
        # contratacion_publica: ya viene bien tipificado desde SECOP → no gastar cuota
        if tipo in EXCLUIDOS_YA_CORRECTOS:
            saltados_secop += 1
            continue
        # solo relevantes con evento concreto de texto libre
        if tipo not in EVENTOS or relev.get("pasa_umbral") is not True:
            saltados_norel += 1
            continue

        # clasificar con el modelo (si Groq agota el cupo, se para ordenadamente)
        try:
            etiqueta, _ = motor.clasificar(_texto(d))
        except Exception as ex:  # noqa: BLE001
            if "429" in str(ex) or "rate_limit" in str(ex).lower():
                print(f"\n  Groq: límite alcanzado. Rate limit: {str(ex)[:300]}")
                print("  Lo ya clasificado quedó guardado; lo pendiente sigue en la próxima corrida.")
                limite_groq = True
                break
            raise
        procesados += 1

        # opción 3: reemplazar tipo_senal, guardar original en tipo_senal_reglas
        clasif["tipo_senal_reglas"] = tipo
        clasif["metodo_reglas"] = clasif.get("metodo", "reglas")
        if etiqueta != tipo:
            cambiados += 1
        clasif["tipo_senal"] = etiqueta
        clasif["metodo"] = f"{cfg.MOTOR}/{cfg.MODELO_GROQ}"
        clasif["reclasificado_ts_utc"] = datetime.now(timezone.utc).isoformat()
        d["clasificacion"] = clasif

        # escribir el blob actualizado (overwrite)
        blob_cli.upload_blob(json.dumps(d, ensure_ascii=False).encode("utf-8"),
                             overwrite=True)

        flecha = "→" if etiqueta != tipo else "="
        print(f"  [{procesados}] {tipo} {flecha} {etiqueta}  | {(d.get('titulo','') or '')[:50]}")
        if cfg.MOTOR == "groq":
            time.sleep(cfg.GROQ_PAUSA_SEG)

    resumen = {"contenedor": contenedor, "procesados": procesados,
               "cambiados": cambiados, "ya_procesados": saltados_ya,
               "secop_conservados": saltados_secop, "no_relevantes": saltados_norel,
               "limite_groq": limite_groq}
    print(f"\n  → procesados={procesados}  cambiados={cambiados}  "
          f"ya_estaban={saltados_ya}  secop_conservados={saltados_secop}  "
          f"no_aplican={saltados_norel}")
    return resumen


def main() -> None:
    from azure.storage.blob import BlobServiceClient

    args = sys.argv[1:]
    todos = "--todos" in args
    limite_total = None
    if "--limite" in args:
        i = args.index("--limite")
        limite_total = int(args[i + 1])
        del args[i:i + 2]
    args = [a for a in args if not a.startswith("--")]
    tope = None
    objetivo = CONTENEDORES if todos else []
    if not todos:
        if not args:
            print("Uso: python clasificar_eventos.py <silver-contenedor> [tope]")
            print("     python clasificar_eventos.py --todos")
            sys.exit(1)
        objetivo = [args[0]]
        if len(args) > 1 and args[1].isdigit():
            tope = int(args[1])

    blob_svc = BlobServiceClient.from_connection_string(_conn())
    motor = clasificador.obtener_motor()
    print(f"Motor: {cfg.MOTOR} / {cfg.MODELO_GROQ}")

    resumenes = []
    restante = limite_total
    for c in objetivo:
        if restante is not None and restante <= 0:
            print(f"\nTope de {limite_total} por corrida alcanzado; lo pendiente sigue en la próxima.")
            break
        tope_c = tope if restante is None else (restante if tope is None else min(tope, restante))
        r = procesar_contenedor(blob_svc, c, tope_c, motor)
        resumenes.append(r)
        if restante is not None:
            restante -= r["procesados"]
        if r["limite_groq"]:
            break

    print(f"\n{'='*60}\nRESUMEN FINAL\n{'='*60}")
    tot_p = sum(r["procesados"] for r in resumenes)
    tot_c = sum(r["cambiados"] for r in resumenes)
    for r in resumenes:
        print(f"  {r['contenedor']:28} procesados={r['procesados']:4} cambiados={r['cambiados']:4}")
    print(f"\n  TOTAL procesados={tot_p}  cambiados={tot_c}")
    if tot_p:
        print(f"  El modelo cambió la etiqueta en {100*tot_c/tot_p:.0f}% de los eventos.\n")


if __name__ == "__main__":
    main()