"""
test_clientes.py
================
Diagnóstico del KIT 2 (clientes) contra las APIs REALES.

Objetivo: ver, cliente por cliente y fuente por fuente, cuántos items trae
cada uno y con qué estado — para detectar nombres de SECOP que hay que
ajustar (consulta vacía) o errores de campo (excepción), ANTES de depender
de esta capa para Silver/Gold. Sirve además como evidencia de prueba de
calidad de Bronze para el jurado.

Cómo se usa (desde la raíz del proyecto, donde está local.settings.json):

  python test_clientes.py
      → prueba GRATIS (SECOP + Google News) para los 19 clientes.

  python test_clientes.py --cliente DANE
      → prueba un solo cliente por nombre.

  python test_clientes.py --web
      → incluye scraping web (Firecrawl → CUESTA crédito).

  python test_clientes.py --jobs
      → incluye Parte B: licitaciones + LinkedIn Jobs (Apify → CUESTA crédito).

  python test_clientes.py --ingesta
      → ejecuta la INGESTA REAL (guarda blobs en bronze-clientes y mueve
        cursores delta). Sin esta bandera, solo consulta y cuenta, no guarda.

Se pueden combinar: python test_clientes.py --cliente Claro --web
"""

import os
import sys
import json
import argparse

# ─────────────────────────────────────────────────────────────
# 1) Cargar variables de entorno desde local.settings.json
# ─────────────────────────────────────────────────────────────
def cargar_entorno():
    ruta = "local.settings.json"
    if not os.path.exists(ruta):
        print(f"[AVISO] No encontré {ruta}. Asegúrate de correr esto desde la "
              f"raíz del proyecto (donde vive local.settings.json).")
        return
    with open(ruta, encoding="utf-8") as f:
        settings = json.load(f)
    for k, v in settings.get("Values", {}).items():
        os.environ[k] = str(v)
    print(f"[OK] Variables de entorno cargadas desde {ruta}")


cargar_entorno()
sys.path.insert(0, ".")

# Import después de cargar el entorno (el módulo instancia clientes al importar)
try:
    from ingestion_clientes import function_clientes as fc
except Exception as ex:
    print(f"[ERROR] No pude importar function_clientes: {ex}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# Utilidades de impresión
# ─────────────────────────────────────────────────────────────
def linea(char="─", n=72):
    print(char * n)


def contar(fn, *args, **kwargs):
    """Ejecuta una llamada de fuente y devuelve (n_items, estado_texto)."""
    try:
        items = fn(*args, **kwargs) or []
        return len(items), "ok" if items else "vacío"
    except Exception as ex:
        return 0, f"ERROR: {type(ex).__name__}: {ex}"


# ─────────────────────────────────────────────────────────────
# 2) DIAGNÓSTICO DE FUENTES (no guarda nada — solo consulta y cuenta)
#    Llama a las fuentes crudas, SIN pasar por delta/storage, para ver
#    la verdad de cada fuente en cada corrida.
# ─────────────────────────────────────────────────────────────
def diagnostico_cliente(cli: dict, incluir_web: bool) -> dict:
    nombre = cli.get("nombre", "?")
    tipo   = cli.get("tipo", "?")
    secop_nombres = cli.get("secop_nombres") or []
    fila = {"nombre": nombre, "tipo": tipo, "fuentes": {}, "alertas": []}

    # --- SECOP según tipo ---
    if tipo == "publico":
        tot_p, tot_c = 0, 0
        for sn in secop_nombres:
            n_p, _ = contar(fc.secop.get_procesos_por_entidad, sn, limite=100)
            n_c, _ = contar(fc.secop.get_contratos_por_entidad, sn, limite=100)
            tot_p += n_p
            tot_c += n_c
        fila["fuentes"]["secop_procesos"]  = tot_p
        fila["fuentes"]["secop_contratos"] = tot_c
        if tot_p == 0 and tot_c == 0:
            fila["alertas"].append("SECOP=0 → revisar nombre(s) de entidad")

    elif tipo == "ti_privado":
        tot_a = 0
        for sn in secop_nombres:
            n_a, _ = contar(fc.secop.get_contratos_competidor, sn, 50)
            tot_a += n_a
        fila["fuentes"]["secop_adjudicatario"] = tot_a
        if tot_a == 0:
            fila["alertas"].append("SECOP adjudicatario=0 → nombre muy genérico o sin contratos")

    # --- Noticias (Google News por nombre) ---
    query = cli.get("noticias_query") or nombre
    n_news, est_news = contar(fc.news.buscar_noticias_empresa, query, 10)
    fila["fuentes"]["noticias"] = n_news
    if "ERROR" in est_news:
        fila["alertas"].append(f"Noticias {est_news}")
    elif n_news == 0:
        fila["alertas"].append("Noticias=0 → revisar noticias_query")

    # --- Web (opcional, cuesta Firecrawl) ---
    web = cli.get("web")
    if incluir_web and web:
        try:
            crudo  = fc.firecrawl.scrape_many([web]) or []
            limpio = fc._solo_status_ok(crudo)
            fila["fuentes"]["web"] = len(limpio)
            if len(crudo) and not limpio:
                fila["alertas"].append("Web: todo descartado por statusCode≠200")
        except Exception as ex:
            fila["fuentes"]["web"] = 0
            fila["alertas"].append(f"Web ERROR: {type(ex).__name__}")
    elif web:
        fila["fuentes"]["web"] = "(omitida, usa --web)"

    return fila


def imprimir_fila(fila: dict):
    print(f"\n▶ {fila['nombre']}  [{fila['tipo']}]")
    for fuente, n in fila["fuentes"].items():
        print(f"    {fuente:<22} {n}")
    if fila["alertas"]:
        for a in fila["alertas"]:
            print(f"    ⚠️  {a}")
    else:
        print("    ✅ sin alertas")


def run_diagnostico(clientes: list[dict], incluir_web: bool):
    linea("═")
    print("DIAGNÓSTICO DE FUENTES POR CLIENTE (no guarda nada)")
    linea("═")

    filas = []
    for cli in clientes:
        fila = diagnostico_cliente(cli, incluir_web)
        imprimir_fila(fila)
        filas.append(fila)

    # Resumen
    linea("═")
    print("RESUMEN")
    linea("═")
    con_alertas = [f["nombre"] for f in filas if f["alertas"]]
    sin_secop = [
        f["nombre"] for f in filas
        if f["tipo"] in ("publico", "ti_privado")
        and sum(v for k, v in f["fuentes"].items()
                if k.startswith("secop") and isinstance(v, int)) == 0
    ]
    sin_noticias = [f["nombre"] for f in filas
                    if isinstance(f["fuentes"].get("noticias"), int)
                    and f["fuentes"]["noticias"] == 0]

    print(f"Clientes probados:        {len(filas)}")
    print(f"Con alguna alerta:        {len(con_alertas)}")
    if sin_secop:
        print(f"SECOP vacío (ajustar nombre): {', '.join(sin_secop)}")
    if sin_noticias:
        print(f"Noticias vacío:           {', '.join(sin_noticias)}")
    if not con_alertas:
        print("✅ Todos los clientes trajeron datos en todas sus fuentes.")


# ─────────────────────────────────────────────────────────────
# 3) INGESTA REAL (guarda en bronze-clientes) — solo con --ingesta
# ─────────────────────────────────────────────────────────────
def run_ingesta(clientes: list[dict], incluir_jobs: bool):
    linea("═")
    print("INGESTA REAL → guarda blobs en bronze-clientes y mueve cursores")
    linea("═")

    fc._ensure_tables()

    n = len(clientes)
    print(f"\n[Parte A] Vigilancia de {n} cliente(s) conocido(s)…")
    resultados = []
    for cli in clientes:
        try:
            resultados.extend(fc._monitorear_cliente(cli))
        except Exception as ex:
            resultados.append({"empresa": cli.get("nombre", "?"),
                               "status": "error", "detalle": str(ex)})
    ok  = sum(1 for r in resultados if r.get("status") in ("ok", "ok_con_errores"))
    new = sum(r.get("items_new", 0) for r in resultados)
    print(f"    Resultados: {len(resultados)} | con datos nuevos: {ok} | items nuevos: {new}")
    for r in resultados:
        if r.get("items_new", 0) or r.get("status") not in ("no_data", "skip_delta",
                                                             "sin_fuentes_activas"):
            print(f"      - {r.get('empresa','?'):<28} {r.get('status'):<18} "
                  f"new={r.get('items_new',0)}")

    if incluir_jobs:
        print("\n[Parte B] Licitaciones TI abiertas (SECOP)…")
        print("   ", fc.ingest_secop_licitaciones())
        print("\n[Parte B] LinkedIn Jobs (Apify — cuesta crédito)…")
        print("   ", fc.ingest_linkedin_jobs())
    else:
        print("\n[Parte B] Omitida (usa --jobs para incluir licitaciones + LinkedIn Jobs).")


# ─────────────────────────────────────────────────────────────
# 4) Main
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Diagnóstico del KIT 2 (clientes).")
    parser.add_argument("--cliente", help="Probar un solo cliente por nombre exacto.")
    parser.add_argument("--web", action="store_true", help="Incluir scraping web (Firecrawl, cuesta).")
    parser.add_argument("--jobs", action="store_true", help="Incluir Parte B (licitaciones + LinkedIn Jobs, cuesta).")
    parser.add_argument("--ingesta", action="store_true", help="Ejecutar la ingesta REAL (guarda blobs).")
    args = parser.parse_args()

    clientes = fc.get_clientes(solo_activos=False)
    if not clientes:
        print("[ERROR] clientes.json vacío o no encontrado en ingestion_clientes/.")
        sys.exit(1)

    if args.cliente:
        clientes = [c for c in clientes if c.get("nombre", "").lower() == args.cliente.lower()]
        if not clientes:
            print(f"[ERROR] Cliente '{args.cliente}' no está en clientes.json.")
            sys.exit(1)

    if args.ingesta:
        run_ingesta(clientes, incluir_jobs=args.jobs)
    else:
        run_diagnostico(clientes, incluir_web=args.web)
        print("\n(ℹ️  Esto solo consultó y contó. Para GUARDAR en bronze-clientes usa --ingesta.)")


if __name__ == "__main__":
    main()