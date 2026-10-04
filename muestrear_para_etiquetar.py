# -*- coding: utf-8 -*-
"""
muestrear_para_etiquetar.py
===========================
QUE HACE: toma una muestra aleatoria de registros ya procesados en los
contenedores Silver y genera un CSV para etiquetar a mano el tema y el
sentimiento correctos de cada uno.

PARA QUE SIRVE: construir el CONJUNTO DE VALIDACION, la "verdad de referencia"
contra la cual se medira la precision de las reglas y (luego) de los modelos.
Es el activo central del experimento comparativo. Se hace UNA sola vez.

QUE PRODUCE: un archivo 'conjunto_validacion.csv' con estas columnas:
  - id, contenedor, familia         -> identifican el registro
  - titulo, descripcion             -> lo que lees para decidir
  - tema_predicho, sent_predicho    -> lo que dijo el sistema (NO lo copies)
  - tema_real, sent_real            -> VACIAS: aqui escribes tu la verdad

USO:
    python muestrear_para_etiquetar.py

Ajusta abajo N_POR_CONTENEDOR y CONTENEDORES segun necesites.
"""
import os
import sys
import csv
import json
import random

from azure.storage.blob import BlobServiceClient

# --- Configuracion del muestreo ---
# Solo se muestrean familias donde el tema/sentimiento son ambiguos y vale la
# pena medir (noticias y posts). Las familias deterministas (SECOP, GitHub...)
# no se incluyen porque su clasificacion es 100% correcta por construccion.
FAMILIAS_A_MEDIR = {
    "noticia_rss", "linkedin_post", "facebook", "instagram", "tiktok", "x", "youtube"
}
N_TOTAL = 80            # cuantos registros quieres etiquetar en total
CONTENEDORES = [
    "silver-competidores", "silver-clientes", "silver-clientes-competencia",
    "silver-mercado", "silver-tendencias",
]
SALIDA = "conjunto_validacion.csv"


def obtener_conexion():
    conn = os.environ.get("AzureWebJobsStorage", "")
    if conn:
        return conn
    try:
        with open("local.settings.json", encoding="utf-8") as f:
            return json.load(f).get("Values", {}).get("AzureWebJobsStorage", "")
    except Exception as e:
        print(f"No se pudo leer local.settings.json: {e}")
        return ""


def main():
    conn = obtener_conexion()
    if not conn:
        print("ERROR: no se encontro la cadena de conexion (AzureWebJobsStorage).")
        sys.exit(1)

    bs = BlobServiceClient.from_connection_string(conn)

    # 1) Recolectar candidatos de las familias que interesan
    candidatos = []
    for nombre_cont in CONTENEDORES:
        cc = bs.get_container_client(nombre_cont)
        try:
            for blob in cc.list_blobs():
                # el nombre del blob es familia/id.json -> filtramos por prefijo
                familia = blob.name.split("/")[0]
                if familia in FAMILIAS_A_MEDIR:
                    candidatos.append((nombre_cont, blob.name))
        except Exception as e:
            print(f"  (aviso) no se pudo listar {nombre_cont}: {e}")

    print(f"Candidatos encontrados (familias medibles): {len(candidatos)}")
    if not candidatos:
        print("No hay candidatos. Revisa que los contenedores Silver existan y tengan datos.")
        sys.exit(1)

    # 2) Muestra aleatoria
    random.shuffle(candidatos)
    muestra = candidatos[:N_TOTAL]
    print(f"Tomando muestra de {len(muestra)} registros para etiquetar.\n")

    # 3) Descargar cada uno y volcar al CSV
    filas = []
    for nombre_cont, blob_name in muestra:
        cc = bs.get_container_client(nombre_cont)
        try:
            data = cc.get_blob_client(blob_name).download_blob().readall()
            reg = json.loads(data)
        except Exception:
            continue
        filas.append({
            "id": reg.get("id", ""),
            "contenedor": nombre_cont,
            "familia": reg.get("familia", ""),
            "titulo": (reg.get("titulo") or "")[:200],
            "descripcion": (reg.get("descripcion") or "")[:500],
            "tema_predicho": reg.get("clasificacion", {}).get("tipo_senal", ""),
            "sent_predicho": (reg.get("sentimiento") or {}).get("etiqueta", ""),
            "tema_real": "",   # <- lo llenas tu
            "sent_real": "",   # <- lo llenas tu
        })

    columnas = ["id", "contenedor", "familia", "titulo", "descripcion",
                "tema_predicho", "sent_predicho", "tema_real", "sent_real"]
    with open(SALIDA, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        w.writerows(filas)

    print(f"Listo: {SALIDA} generado con {len(filas)} filas.")
    print("Abrelo en Excel y llena las columnas 'tema_real' y 'sent_real'.")


if __name__ == "__main__":
    main()