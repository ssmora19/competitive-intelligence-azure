"""
orquestador.py
==============
Ejecuta el pipeline completo de una sola vez: BRONZE -> SILVER -> GOLD.
Pensado para el demo con el cliente: un solo comando lanza todo el proceso,
sin ir mandando capa por capa.

Lee la configuración de 'parametros_monitoreo.json' (mismo folder). Para
cambiar QUÉ se ejecuta, editas ese JSON, no este archivo.

CÓMO CORRE CADA CAPA
--------------------
  BRONZE  -> subproceso: python ejecutar_todo.py  (toca los endpoints HTTP)
  SILVER  -> 1) HTTP /api/silver/procesar, por contenedor y por tandas
             2) pasos_post (subprocesos): p.ej. clasificación de eventos (Groq)
  GOLD    -> subproceso: python calcular_gold.py  (en la carpeta gold/)

REQUISITO PREVIO (una sola vez, se deja corriendo):
  En OTRA terminal, parado en la raíz del proyecto:
      func start
  Es el servidor local que hace el trabajo de Bronze y Silver. El orquestador
  verifica que esté arriba antes de empezar; si no lo está, te avisa y sale.

USO:
  python orquestador.py

Es "fail-fast": si una fase falla, se detiene ahí y NO sigue con la siguiente
(no tiene sentido calcular Gold sobre un Silver incompleto).
"""

import os
import sys
import json
import time
import socket
import subprocess

try:
    import requests
except ImportError:
    print("[ERROR] Falta 'requests'. Instala: pip install requests")
    sys.exit(1)

RAIZ = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(RAIZ, "parametros_monitoreo.json")
PYTHON = sys.executable  # el mismo intérprete/venv con que se lanzó esto

FUNC_HOST = "localhost"
FUNC_PORT = 7071
BASE = f"http://{FUNC_HOST}:{FUNC_PORT}/api"
TIMEOUT_TANDA = 900  # 15 min por tanda de Silver


# ─────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────

def cargar_config() -> dict:
    if not os.path.exists(CONFIG):
        print(f"[ERROR] No encuentro '{CONFIG}'.")
        print("        Debe estar en la misma carpeta que orquestador.py.")
        sys.exit(1)
    with open(CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def func_esta_arriba() -> bool:
    """True si el servidor local de Functions responde en el puerto 7071."""
    try:
        with socket.create_connection((FUNC_HOST, FUNC_PORT), timeout=3):
            return True
    except OSError:
        return False


def correr(nombre: str, cmd: list, cwd: str) -> bool:
    """Ejecuta un comando (subproceso) y devuelve True si terminó bien."""
    print("\n" + "─" * 60)
    print(f"  {nombre}")
    print(f"  cmd: {' '.join(cmd)}   (cwd: {os.path.relpath(cwd, RAIZ)})")
    print("─" * 60)
    inicio = time.time()
    resultado = subprocess.run(cmd, cwd=cwd)
    dur = time.time() - inicio
    if resultado.returncode == 0:
        print(f"  OK  ({dur:.0f}s)")
        return True
    print(f"  FALLO (exit {resultado.returncode}, {dur:.0f}s)")
    return False


def correr_paso(titulo: str, paso: dict) -> bool:
    """Ejecuta un 'paso' del JSON: {script, cwd, args}."""
    nombre = paso.get("nombre", paso["script"])
    cwd = os.path.join(RAIZ, paso.get("cwd", "."))
    cmd = [PYTHON, paso["script"]] + list(paso.get("args", []))
    return correr(f"{titulo} — {nombre}", cmd, cwd=cwd)


# ─────────────────────────────────────────────────────────────
# FASE 1 — BRONZE (subproceso a ejecutar_todo.py)
# ─────────────────────────────────────────────────────────────

def fase_bronze(cfg: dict) -> bool:
    b = cfg.get("bronze", {})
    cmd = [PYTHON, "ejecutar_todo.py"]
    if b.get("incluir_apify"):
        cmd.append("--apify")
    if b.get("incluir_opcional"):
        cmd.append("--opcional")
    if b.get("solo_contenedor"):
        cmd += ["--contenedor", b["solo_contenedor"]]
    return correr("FASE 1 — BRONZE (ingesta)", cmd, cwd=RAIZ)


# ─────────────────────────────────────────────────────────────
# FASE 2 — SILVER (HTTP por contenedor + tandas, luego pasos_post)
# ─────────────────────────────────────────────────────────────

def procesar_contenedor_silver(contenedor: str, tam: int, solo_relevantes: bool):
    """Procesa TODO un contenedor Bronze por tandas. Devuelve (ok, acumulado)."""
    desde = 0
    acc = {"escritos": 0, "skip_existente": 0, "skip_duplicado": 0,
           "skip_no_relevante": 0, "sin_familia": 0, "errores": 0}
    while True:
        hasta = desde + tam
        url = f"{BASE}/silver/procesar?contenedor={contenedor}&desde={desde}&hasta={hasta}"
        if solo_relevantes:
            url += "&solo_relevantes=1"
        try:
            r = requests.get(url, timeout=TIMEOUT_TANDA)
        except requests.exceptions.ConnectionError:
            print("     ERROR: sin conexión a localhost:7071")
            return False, acc
        except requests.exceptions.ReadTimeout:
            print(f"     timeout en tanda {desde}-{hasta} (puede seguir en el servidor)")
            return False, acc
        except Exception as ex:
            print(f"     ERROR: {ex}")
            return False, acc

        if r.status_code != 200:
            print(f"     HTTP {r.status_code}: {r.text[:200]}")
            return False, acc

        data = r.json()
        total = data.get("total_en_contenedor", 0)
        en_tanda = data.get("en_tanda", 0)
        for k in acc:
            acc[k] += data.get(k, 0)
        print(f"     tanda {desde}-{hasta}: escritos={data.get('escritos', 0)} "
              f"skip_exist={data.get('skip_existente', 0)} "
              f"errores={data.get('errores', 0)}  (total contenedor: {total})")

        desde = hasta
        if en_tanda == 0 or desde >= total:
            break
    return True, acc


def fase_silver(cfg: dict) -> bool:
    s = cfg.get("silver", {})
    contenedores = s.get("contenedores", [])
    tam = s.get("tam_tanda", 50)
    solo_rel = s.get("solo_relevantes", False)

    print("\n" + "─" * 60)
    print("  FASE 2 — SILVER (clasificación / calidad)")
    print("─" * 60)

    if not contenedores:
        print("  [AVISO] No hay contenedores Silver configurados en el JSON.")
    else:
        total_escritos = 0
        for c in contenedores:
            print(f"\n  > {c}")
            ok, acc = procesar_contenedor_silver(c, tam, solo_rel)
            if not ok:
                print(f"  FALLO procesando '{c}'.")
                return False
            print(f"    resumen {c}: escritos={acc['escritos']} "
                  f"skip_exist={acc['skip_existente']} sin_familia={acc['sin_familia']} "
                  f"errores={acc['errores']}")
            total_escritos += acc["escritos"]
        print(f"\n  Silver (HTTP) OK — total escritos: {total_escritos}")

    # Pasos posteriores (subprocesos): p.ej. clasificación de eventos con Groq.
    for paso in s.get("pasos_post", []):
        if not correr_paso("FASE 2 — SILVER (post)", paso):
            return False

    return True


# ─────────────────────────────────────────────────────────────
# FASE 3 — GOLD (subproceso)
# ─────────────────────────────────────────────────────────────

def fase_gold(cfg: dict) -> bool:
    for paso in cfg.get("gold", []):
        if not correr_paso("FASE 3 — GOLD", paso):
            return False
    return True


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    cfg = cargar_config()

    print("═" * 60)
    print("  ORQUESTADOR — pipeline completo Bronze -> Silver -> Gold")
    print("═" * 60)

    if not func_esta_arriba():
        print("\n[STOP] El servidor local de Functions no esta corriendo.")
        print("       Abre OTRA terminal, parate en la raiz del proyecto y corre:")
        print("           func start")
        print("       Deja esa terminal abierta y vuelve a lanzar el orquestador.")
        sys.exit(1)
    print("\n  Servidor local de Functions detectado (localhost:7071).")

    t0 = time.time()

    if not fase_bronze(cfg):
        print("\n[STOP] Bronze fallo. No sigo con Silver.")
        sys.exit(1)

    if not fase_silver(cfg):
        print("\n[STOP] Silver fallo. No sigo con Gold.")
        sys.exit(1)

    if not fase_gold(cfg):
        print("\n[STOP] Gold fallo.")
        sys.exit(1)

    total = time.time() - t0
    print("\n" + "═" * 60)
    print(f"  PIPELINE COMPLETO  —  {total/60:.1f} min")
    print("═" * 60)
    print("  Bronze -> Silver -> Gold ejecutados de principio a fin.")
    print("  Ya puedes refrescar los dashboards de Power BI.")


if __name__ == "__main__":
    main()