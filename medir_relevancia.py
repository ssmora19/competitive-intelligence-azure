# -*- coding: utf-8 -*-
"""
medir_relevancia.py
===================
QUE HACE: cruza tu etiqueta humana de relevancia (relevante_real: si/no) contra
la decision del sistema (relevancia.pasa_umbral) para cada registro del conjunto
de validacion. Lee el pasa_umbral real desde los blobs Silver en Azure.

PARA QUE SIRVE: responde la pregunta clave -> ¿el filtro de relevancia atrapa el
ruido, o lo deja pasar a Gold? Produce una matriz de confusion con los 4 casos:
  - Acierta relevante  : tu dices SI, sistema deja pasar  (bien)
  - Acierta ruido      : tu dices NO, sistema filtra       (bien)
  - RUIDO COLADO       : tu dices NO, sistema deja pasar    (problema: ensucia Gold)
  - SEÑAL PERDIDA      : tu dices SI, sistema filtra        (problema: pierde valor)

USO:
    python medir_relevancia.py conjunto_validacion_final.csv
"""
import os, sys, csv, json

from azure.storage.blob import BlobServiceClient


def conexion():
    c = os.environ.get("AzureWebJobsStorage", "")
    if c:
        return c
    with open("local.settings.json", encoding="utf-8") as f:
        return json.load(f).get("Values", {}).get("AzureWebJobsStorage", "")


def main():
    archivo = sys.argv[1] if len(sys.argv) > 1 else "conjunto_validacion_final.csv"
    with open(archivo, encoding="utf-8-sig") as f:
        filas = [r for r in csv.DictReader(f) if r.get("relevante_real", "").strip()]

    bs = BlobServiceClient.from_connection_string(conexion())

    # Cache de contenedores
    cont_cache = {}
    def cc(nombre):
        if nombre not in cont_cache:
            cont_cache[nombre] = bs.get_container_client(nombre)
        return cont_cache[nombre]

    # Contadores de la matriz de confusion
    acierta_rel = acierta_ruido = ruido_colado = senal_perdida = 0
    no_encontrados = 0
    detalle_problemas = []

    for r in filas:
        contenedor = r["contenedor"]
        familia = r["familia"]
        rid = r["id"]
        real_si = r["relevante_real"].strip().lower() == "si"

        # El blob Silver se guarda como familia/id.json
        blob_name = f"{familia}/{rid}.json"
        try:
            data = cc(contenedor).get_blob_client(blob_name).download_blob().readall()
            reg = json.loads(data)
        except Exception:
            no_encontrados += 1
            continue

        pasa = reg.get("relevancia", {}).get("pasa_umbral", True)

        if real_si and pasa:
            acierta_rel += 1
        elif (not real_si) and (not pasa):
            acierta_ruido += 1
        elif (not real_si) and pasa:
            ruido_colado += 1
            detalle_problemas.append(("RUIDO COLADO", r["titulo"][:60]))
        elif real_si and (not pasa):
            senal_perdida += 1
            detalle_problemas.append(("SENAL PERDIDA", r["titulo"][:60]))

    total = acierta_rel + acierta_ruido + ruido_colado + senal_perdida
    print("="*60)
    print("MEDICION DE RELEVANCIA (filtro del sistema vs tu criterio)")
    print("="*60)
    print(f"Registros cruzados: {total}   (no encontrados en Silver: {no_encontrados})\n")
    print(f"  Acierta relevante (SI->pasa)  : {acierta_rel}")
    print(f"  Acierta ruido     (NO->filtra): {acierta_ruido}")
    print(f"  >> RUIDO COLADO   (NO->pasa)  : {ruido_colado}   <- ensucia Gold")
    print(f"  >> SENAL PERDIDA  (SI->filtra): {senal_perdida}   <- pierde valor")
    if total:
        aciertos = acierta_rel + acierta_ruido
        print(f"\n  Exactitud del filtro: {aciertos}/{total} = {100*aciertos/total:.1f}%")
        if (acierta_ruido + ruido_colado):
            print(f"  Ruido efectivamente filtrado: {acierta_ruido}/{acierta_ruido+ruido_colado} = {100*acierta_ruido/(acierta_ruido+ruido_colado):.1f}%")

    print("\nProblemas detectados:")
    for tipo, tit in detalle_problemas:
        print(f"  [{tipo}] {tit!r}")


if __name__ == "__main__":
    main()