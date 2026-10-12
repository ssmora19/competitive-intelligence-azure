"""
ejecutar_todo.py
================
Dispara la ingesta de los 7 contenedores llamando sus endpoints HTTP locales.
Es el equivalente a tus tests viejos, pero apuntando a las Azure Functions reales.

⚠️ REQUISITO: tener `func start` corriendo en OTRA terminal (es el servidor local
   que hace el trabajo). Este script solo "toca el timbre" de cada fuente.

USO:
  # Solo las fuentes GRATIS (SECOP, GitHub, RSS, Google News, Firecrawl web):
  python ejecutar_todo.py

  # Incluir también las de Apify (redes sociales, LinkedIn Jobs — CUESTAN crédito):
  python ejecutar_todo.py --apify

  # Incluir la fuente opcional de marketplaces (Firecrawl, bajo rendimiento):
  python ejecutar_todo.py --opcional

  # Un solo contenedor:
  python ejecutar_todo.py --contenedor mercado

Nota: cada llamada puede tardar (el scraping web es secuencial). Ten paciencia;
el script espera hasta 15 min por fuente.
"""

import sys
import time
import json
import argparse

try:
    import requests
except ImportError:
    print("[ERROR] Falta 'requests'. Instala: pip install requests")
    sys.exit(1)

BASE = "http://localhost:7071/api"
TIMEOUT = 900          # 15 min por fuente (el scraping web es lento)
PAUSA = 2              # segundos entre llamadas

# ─────────────────────────────────────────────────────────────
# TAREAS: (contenedor, fuente)
# ─────────────────────────────────────────────────────────────

# GRATIS — sin costo de Apify (SECOP, GitHub, RSS, Google News, Firecrawl web).
# Ordenadas: primero las ligeras (APIs/feeds), luego las de scraping web.
TAREAS_GRATIS = [
    # Ligeras (rápidas, sin Firecrawl)
    ("clientes",     "clientes_conocidos"),
    ("clientes",     "secop_licitaciones"),
    ("competidores", "secop_competidores"),
    ("competidores", "github_competidores"),
    ("competidores", "noticias_competidores"),
    ("competidores", "youtube"),
    ("mercado",      "noticias_economicas"),
    ("tendencias",   "github_trending"),
    ("tendencias",   "rss_fabricantes"),
    ("tendencias",   "rss_tech_general"),
    ("tendencias",   "rss_colombia"),
    ("proveedores",  "rss_proveedores"),
    ("proveedores",  "startups_tech_colombia"),
    ("regulatorio",  "noticias_regulatorio"),
    # Scraping web (Firecrawl — cuentan contra los 500/mes gratis)
    ("competidores", "web"),
    ("mercado",      "dane_estadisticas"),
    ("mercado",      "reportes_economicos"),
    ("mercado",      "ontic_barreras_adopcion"),
    ("proveedores",  "web_proveedores_core"),
    ("proveedores",  "licenciamiento"),
    ("proveedores",  "noticias_proveedores"),
    ("capacidades",  "web_tak"),
    ("capacidades",  "certificaciones_fabricantes"),
    ("tendencias",   "reportes_sectoriales"),
    ("tendencias",   "documentacion_tecnica"),
    ("tendencias",   "eventos_tech"),
    ("tendencias",   "patentes_tech"),
    ("regulatorio",  "mintic"),
    ("regulatorio",  "normativa_tic"),
    ("regulatorio",  "sic_datos"),
    ("regulatorio",  "colombia_compra"),
    ("regulatorio",  "financiamiento_tech"),
    ("regulatorio",  "inversion_tech"),
]

# APIFY — CUESTAN crédito. Solo se corren con --apify.
TAREAS_APIFY = [
    ("competidores", "instagram"),
    ("competidores", "facebook"),
    ("competidores", "tiktok"),
    ("competidores", "linkedin"),
    ("competidores", "x"),
    ("clientes",     "linkedin_jobs"),
    ("capacidades",  "demanda_perfiles_ti"),
    ("mercado",      "redes_sociales_mercado"),
    ("proveedores",  "redes_sociales_proveedores"),
    ("tendencias",   "redes_sociales_tech"),
]

# OPCIONAL — Firecrawl de bajo rendimiento. Solo con --opcional.
TAREAS_OPCIONAL = [
    ("proveedores",  "marketplaces_tech"),
]


def _resumen_respuesta(texto: str) -> str:
    """Extrae un resumen legible de la respuesta JSON de /ejecutar."""
    try:
        data = json.loads(texto)
    except Exception:
        return texto[:200]

    resultados = data.get("resultados", [])
    if not isinstance(resultados, list):
        return texto[:200]

    total_new = 0
    estados = {}
    for r in resultados:
        total_new += r.get("items_new", 0) or 0
        est = r.get("status", "?")
        estados[est] = estados.get(est, 0) + 1
    return f"items_nuevos={total_new} | detalle={estados}"


def ejecutar_tarea(contenedor: str, fuente: str) -> dict:
    url = f"{BASE}/{contenedor}/ejecutar?fuente={fuente}"
    print(f"\n▶ {contenedor}/{fuente}")
    print(f"   {url}")
    try:
        r = requests.get(url, timeout=TIMEOUT)
    except requests.exceptions.ConnectionError:
        print("   ❌ No pude conectar a localhost:7071.")
        print("      ¿Tienes 'func start' corriendo en otra terminal?")
        return {"contenedor": contenedor, "fuente": fuente, "ok": False, "conn": False}
    except requests.exceptions.ReadTimeout:
        print(f"   ⏱️  timeout ({TIMEOUT}s) — puede seguir corriendo en el servidor.")
        return {"contenedor": contenedor, "fuente": fuente, "ok": False, "timeout": True}
    except Exception as ex:
        print(f"   ❌ error: {ex}")
        return {"contenedor": contenedor, "fuente": fuente, "ok": False}

    if r.status_code == 200:
        resumen = _resumen_respuesta(r.text)
        print(f"   ✅ {resumen}")
        return {"contenedor": contenedor, "fuente": fuente, "ok": True, "resumen": resumen}
    else:
        print(f"   ⚠️  HTTP {r.status_code}: {r.text[:200]}")
        return {"contenedor": contenedor, "fuente": fuente, "ok": False, "http": r.status_code}


def main():
    parser = argparse.ArgumentParser(description="Dispara la ingesta de los contenedores.")
    parser.add_argument("--apify", action="store_true", help="Incluir fuentes de Apify (cuestan crédito).")
    parser.add_argument("--opcional", action="store_true", help="Incluir fuentes opcionales (marketplaces).")
    parser.add_argument("--contenedor", help="Solo un contenedor (ej: mercado).")
    args = parser.parse_args()

    tareas = list(TAREAS_GRATIS)
    if args.opcional:
        tareas += TAREAS_OPCIONAL
    if args.apify:
        tareas += TAREAS_APIFY

    if args.contenedor:
        tareas = [(c, f) for (c, f) in tareas if c == args.contenedor]
        if not tareas:
            print(f"[ERROR] Sin tareas para el contenedor '{args.contenedor}'.")
            sys.exit(1)

    print("═" * 60)
    print(f"  EJECUTAR TODO — {len(tareas)} fuentes")
    print(f"  Apify: {'SÍ' if args.apify else 'no'} | Opcional: {'SÍ' if args.opcional else 'no'}")
    print(f"  (Requiere 'func start' corriendo en otra terminal)")
    print("═" * 60)

    resultados = []
    for contenedor, fuente in tareas:
        res = ejecutar_tarea(contenedor, fuente)
        resultados.append(res)
        # si no hay servidor, no tiene sentido seguir
        if res.get("conn") is False:
            print("\n[STOP] No hay servidor. Levanta 'func start' y vuelve a correr.")
            sys.exit(1)
        time.sleep(PAUSA)

    # Resumen final
    print("\n" + "═" * 60)
    print("  RESUMEN")
    print("═" * 60)
    ok = sum(1 for r in resultados if r.get("ok"))
    fail = len(resultados) - ok
    print(f"  Fuentes OK:    {ok}")
    print(f"  Con problema:  {fail}")
    if fail:
        print("\n  Revisar:")
        for r in resultados:
            if not r.get("ok"):
                motivo = ("timeout" if r.get("timeout") else
                          f"HTTP {r.get('http')}" if r.get("http") else "error")
                print(f"    - {r['contenedor']}/{r['fuente']} ({motivo})")
    print("\n  Tip: revisa cada fuente con /<contenedor>/status?modo=blobs&fuente=<fuente>")


if __name__ == "__main__":
    main()