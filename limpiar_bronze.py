"""
limpiar_bronze.py
=================
Limpia los contenedores Bronze de la basura acumulada en corridas anteriores
(404s duros/blandos, y blobs de fuentes que se eliminaron o renombraron en la
auditoría), y resetea los cursores delta para que la próxima corrida vuelva a
ingerir SOLO datos buenos (ya con el filtro de statusCode activo).

⚠️ SEGURIDAD:
  - Por defecto corre en MODO SIMULACIÓN (dry-run): NO borra nada, solo te
    muestra qué borraría. Debes agregar --aplicar para que borre de verdad.
  - Corre esto desde la RAÍZ del proyecto (donde está local.settings.json).

USO:
  # Simulación de todos los contenedores (no borra, solo muestra):
  python limpiar_bronze.py

  # Simulación de un solo contenedor:
  python limpiar_bronze.py --contenedor bronze-mercado

  # BORRAR de verdad (vaciar) un contenedor y resetear sus cursores:
  python limpiar_bronze.py --contenedor bronze-mercado --aplicar

  # BORRAR de verdad TODOS los contenedores:
  python limpiar_bronze.py --aplicar

  # Vaciar sin resetear cursores (no recomendado):
  python limpiar_bronze.py --aplicar --no-reset-cursores

MODO:
  --modo wipe     (por defecto) vacía el contenedor completo. Como vas a
                  re-ejecutar todo, es la forma de garantizar cero basura.
  --modo orphans  borra SOLO blobs de fuentes que ya no existen (quirúrgico;
                  conserva los datos buenos ya ingeridos). Ver ORPHAN_FUENTES.
"""

import os
import sys
import json
import argparse

# ─────────────────────────────────────────────────────────────
# Contenedores Bronze del sistema
# ─────────────────────────────────────────────────────────────
CONTENEDORES = [
    "bronze-competidores",
    "bronze-clientes",
    "bronze-mercado",
    "bronze-proveedores",
    "bronze-capacidades",
    "bronze-tendencias",
    "bronze-regulatorio",
]

# ─────────────────────────────────────────────────────────────
# Modo 'orphans': etiquetas de fuente que se ELIMINARON o RENOMBRARON
# en la auditoría (producen blobs huérfanos). Solo se usan con --modo orphans.
# El primer segmento del nombre del blob se compara contra esta lista.
# ─────────────────────────────────────────────────────────────
ORPHAN_FUENTES = {
    "bronze-mercado":     ["estudios_mercado_ti"],
    "bronze-capacidades": ["benchmarking_capacidades", "portales_empleo_ti"],
    "bronze-regulatorio": ["web_mintic", "web_colombia_compra",
                            "normativa_legal", "rss_regulatorio"],
    # competidores / clientes / proveedores / tendencias: la basura vieja está
    # mezclada dentro de fuentes que se conservaron (404s blandos), por eso ahí
    # conviene 'wipe' en vez de 'orphans'.
}

# ─────────────────────────────────────────────────────────────
# Cargar variables de entorno desde local.settings.json
# ─────────────────────────────────────────────────────────────
def cargar_entorno():
    ruta = "local.settings.json"
    if not os.path.exists(ruta):
        print(f"[ERROR] No encontré {ruta}. Corre esto desde la raíz del proyecto.")
        sys.exit(1)
    with open(ruta, encoding="utf-8") as f:
        settings = json.load(f)
    for k, v in settings.get("Values", {}).items():
        os.environ[k] = str(v)
    print(f"[OK] Variables de entorno cargadas desde {ruta}")


def _fuente_empresa_de(blob_name: str):
    """Deriva (fuente, empresa) del nombre del blob: 'fuente/empresa/...'."""
    partes = blob_name.split("/")
    fuente = partes[0] if partes else ""
    empresa = partes[1] if len(partes) > 1 else ""
    return fuente, empresa


def limpiar_contenedor(blob_service, nombre_cont, modo, aplicar, muestras=3):
    """
    Devuelve el set de (fuente, empresa) afectadas (para resetear cursores).
    En dry-run no borra; solo cuenta y muestra ejemplos.
    """
    from azure.core.exceptions import ResourceNotFoundError
    try:
        cont = blob_service.get_container_client(nombre_cont)
        blobs = list(cont.list_blobs())
    except ResourceNotFoundError:
        print(f"  [{nombre_cont}] no existe (se omite).")
        return set()
    except Exception as ex:
        print(f"  [{nombre_cont}] error al listar: {ex}")
        return set()

    if not blobs:
        print(f"  [{nombre_cont}] ya está vacío.")
        return set()

    # Decidir qué blobs son candidatos según el modo
    orphan_list = ORPHAN_FUENTES.get(nombre_cont, [])
    a_borrar = []
    for b in blobs:
        fuente, _ = _fuente_empresa_de(b.name)
        if modo == "wipe":
            a_borrar.append(b)
        elif modo == "orphans" and fuente in orphan_list:
            a_borrar.append(b)

    afectadas = set(_fuente_empresa_de(b.name) for b in a_borrar)

    print(f"\n  [{nombre_cont}]")
    print(f"    blobs totales:      {len(blobs)}")
    print(f"    candidatos a borrar: {len(a_borrar)}  (modo={modo})")
    if a_borrar:
        print(f"    fuentes afectadas:   {sorted(set(f for f, _ in afectadas))}")
        print(f"    ejemplos:")
        for b in a_borrar[:muestras]:
            print(f"       - {b.name}")

    if not aplicar:
        return afectadas  # dry-run: no borra

    # Borrado real
    borrados = 0
    for b in a_borrar:
        try:
            cont.delete_blob(b.name)
            borrados += 1
        except Exception as ex:
            print(f"    ! error borrando {b.name}: {ex}")
    print(f"    BORRADOS: {borrados}")
    return afectadas


def resetear_cursores(afectadas_por_cont, aplicar):
    """Resetea los cursores delta de las (fuente, empresa) afectadas."""
    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    try:
        sys.path.insert(0, ".")
        from shared.state_manager import reset_cursor
    except Exception as ex:
        print(f"\n[AVISO] No pude importar shared.state_manager ({ex}).")
        print("        Los blobs se borraron, pero los cursores NO se resetearon.")
        print("        Resetéalos con el endpoint /reset_cursor de cada contenedor.")
        return

    todas = set()
    for pares in afectadas_por_cont.values():
        todas |= pares
    # ignorar pares sin empresa
    todas = {(f, e) for (f, e) in todas if f and e}

    print(f"\n[Cursores] a resetear: {len(todas)}")
    if not aplicar:
        for f, e in sorted(todas):
            print(f"    (simulado) reset {f}/{e}")
        return

    ok = 0
    for f, e in sorted(todas):
        try:
            reset_cursor(conn, f, e)
            ok += 1
        except Exception as ex:
            print(f"    ! error reset {f}/{e}: {ex}")
    print(f"    cursores reseteados: {ok}")


def main():
    parser = argparse.ArgumentParser(description="Limpieza de contenedores Bronze.")
    parser.add_argument("--contenedor", help="Un contenedor específico (default: todos).")
    parser.add_argument("--modo", choices=["wipe", "orphans"], default="wipe",
                        help="wipe = vaciar todo; orphans = solo fuentes eliminadas.")
    parser.add_argument("--aplicar", action="store_true",
                        help="Borra de verdad. Sin esta bandera, solo simula.")
    parser.add_argument("--no-reset-cursores", action="store_true",
                        help="No resetear cursores tras borrar.")
    args = parser.parse_args()

    cargar_entorno()
    conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    if not conn:
        print("[ERROR] Falta AZURE_STORAGE_CONNECTION_STRING en local.settings.json")
        sys.exit(1)

    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        print("[ERROR] Falta azure-storage-blob. Instala: pip install azure-storage-blob")
        sys.exit(1)

    blob_service = BlobServiceClient.from_connection_string(conn)

    objetivos = [args.contenedor] if args.contenedor else CONTENEDORES

    print("\n" + "═" * 60)
    print(f"  MODO: {args.modo}   |   {'APLICAR (borra)' if args.aplicar else 'SIMULACIÓN (no borra)'}")
    print("═" * 60)

    afectadas_por_cont = {}
    for nombre_cont in objetivos:
        afectadas_por_cont[nombre_cont] = limpiar_contenedor(
            blob_service, nombre_cont, args.modo, args.aplicar
        )

    if not args.no_reset_cursores:
        resetear_cursores(afectadas_por_cont, args.aplicar)

    print("\n" + "═" * 60)
    if args.aplicar:
        print("  LISTO. Bronze limpio. Ahora re-ejecuta los contenedores.")
    else:
        print("  Esto fue SIMULACIÓN. Nada se borró.")
        print("  Si el plan de arriba se ve bien, vuelve a correr con --aplicar.")
    print("═" * 60)


if __name__ == "__main__":
    main()