# -*- coding: utf-8 -*-
"""
recalcular_relevancia.py
========================
QUE HACE: recorre los registros de la capa Silver y les vuelve a calcular SOLO el
campo de relevancia con la Regla B mejorada (entidad monitoreada + evento de
negocio), actualizando cada registro en su sitio. No toca normalizacion, entidad
ni calidad (esos ya estan bien); solo corrige la relevancia que se habia calculado
con la regla vieja.

PARA QUE SIRVE: alinear la Silver ya poblada con el filtro de relevancia validado
en el experimento (81%), sin tener que reprocesar los 13.000 blobs de Bronze.
Es el paso previo a clasificar con Gemini: primero la relevancia debe ser la buena.

USO:
    python recalcular_relevancia.py --dry-run      # muestra el cambio sin guardar
    python recalcular_relevancia.py                # aplica y guarda
    python recalcular_relevancia.py --contenedor silver-capacidades   # uno solo
"""
import os
import sys
import json
import argparse

from azure.storage.blob import BlobServiceClient

# Importa la logica de relevancia ya actualizada (Regla B)
from ingestion_silver import enriquecimiento

CONTENEDORES = [
    "silver-competidores", "silver-clientes", "silver-clientes-competencia",
    "silver-tendencias", "silver-proveedores", "silver-regulatorio",
    "silver-mercado", "silver-capacidades",
]


def get_conn():
    c = os.environ.get("AzureWebJobsStorage", "")
    if c:
        return c
    with open("local.settings.json", encoding="utf-8") as f:
        return json.load(f).get("Values", {}).get("AzureWebJobsStorage", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contenedor", type=str, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conts = [args.contenedor] if args.contenedor else CONTENEDORES
    bs = BlobServiceClient.from_connection_string(get_conn())

    # Verificacion previa: que las entidades carguen (si no, la Regla B no sirve)
    from ingestion_silver import entidades
    n_ent = len(entidades.cargar_entidades("."))
    print(f"Entidades monitoreadas cargadas: {n_ent}")
    if n_ent == 0:
        print("ERROR: 0 entidades. Revisa las rutas de los JSON antes de continuar.")
        sys.exit(1)
    print()

    tot_antes_rel = tot_despues_rel = 0
    tot = 0
    cambios = 0

    for cont in conts:
        cc = bs.get_container_client(cont)
        try:
            blobs = list(cc.list_blobs())
        except Exception as e:
            print(f"(no se pudo listar {cont}: {str(e)[:50]})")
            continue

        c_antes = c_despues = 0
        for b in blobs:
            bc = cc.get_blob_client(b.name)
            try:
                reg = json.loads(bc.download_blob().readall())
            except Exception:
                continue
            tot += 1

            antes = reg.get("relevancia", {}).get("pasa_umbral", False)
            if antes:
                c_antes += 1

            # Recalcular relevancia con la Regla B sobre el registro ya normalizado
            nueva_rel = enriquecimiento.puntuar_relevancia(reg)
            despues = nueva_rel.get("pasa_umbral", False)
            if despues:
                c_despues += 1

            if antes != despues:
                cambios += 1

            if not args.dry_run:
                reg["relevancia"] = nueva_rel
                try:
                    bc.upload_blob(json.dumps(reg, ensure_ascii=False).encode("utf-8"),
                                   overwrite=True)
                except Exception as e:
                    print(f"  (no se pudo guardar {b.name}: {str(e)[:40]})")

        tot_antes_rel += c_antes
        tot_despues_rel += c_despues
        print(f"  {cont:32} relevantes: antes={c_antes:5}  despues={c_despues:5}")

    print()
    print("="*60)
    modo = "DRY-RUN (no se guardo nada)" if args.dry_run else "APLICADO (guardado)"
    print(f"RESULTADO [{modo}]")
    print("="*60)
    print(f"  Total registros:        {tot}")
    print(f"  Relevantes ANTES (vieja): {tot_antes_rel}")
    print(f"  Relevantes DESPUES (Regla B): {tot_despues_rel}")
    print(f"  Registros que cambiaron de decision: {cambios}")
    if args.dry_run:
        print("\n  Esto fue una simulacion. Corre sin --dry-run para aplicar.")


if __name__ == "__main__":
    main()