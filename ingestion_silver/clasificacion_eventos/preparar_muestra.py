# -*- coding: utf-8 -*-
"""
preparar_muestra.py
===================
Prepara la muestra para validar la clasificación de eventos. Recorre los
contenedores con más eventos, saca registros RELEVANTES cuyo tipo_senal es un
evento concreto, y genera un CSV para etiquetado con:
  - id, contenedor, titulo, descripcion
  - evento_reglas : lo que clasificaron las reglas de Silver
  - evento_modelo : (se llena luego con el modelo, en la validación)
  - evento_real   : VACÍA — la verdad de terreno que define el humano

Ejecuta:  python preparar_muestra.py
          python preparar_muestra.py 60      (cuántos registros en total)

Genera:   muestra_eventos.csv
"""
from __future__ import annotations
import os, sys, csv, json
from collections import defaultdict

# Contenedores con eventos jugosos (empresas reales), y cuántos sacar de cada uno.
REPARTO = {
    "silver-competidores": 20,
    "silver-clientes-competencia": 15,
    "silver-clientes": 10,
    "silver-mercado": 5,
}

EVENTOS = {"alianza", "lanzamiento", "expansion", "inversion",
           "contratacion_publica", "regulatorio", "actividad_tecnica"}


def _conn():
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


def main():
    total_objetivo = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 50
    # escala el reparto al total pedido
    suma = sum(REPARTO.values())
    reparto = {c: max(1, round(n * total_objetivo / suma)) for c, n in REPARTO.items()}

    from azure.storage.blob import BlobServiceClient
    svc = BlobServiceClient.from_connection_string(_conn())

    filas = []
    for contenedor, cupo in reparto.items():
        try:
            cli = svc.get_container_client(contenedor)
        except Exception as e:
            print(f"  (no pude abrir {contenedor}: {e})")
            continue
        # variedad: intenta no repetir demasiado el mismo tipo
        por_tipo = defaultdict(int)
        tomados = 0
        for blob in cli.list_blobs():
            if tomados >= cupo:
                break
            try:
                d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
            except Exception:
                continue
            clasif = d.get("clasificacion", {}) or {}
            relev = d.get("relevancia", {}) or {}
            tipo = clasif.get("tipo_senal")
            if tipo not in EVENTOS or relev.get("pasa_umbral") is not True:
                continue
            # límite suave por tipo para tener variedad (máx la mitad del cupo de un tipo)
            if por_tipo[tipo] >= max(2, cupo // 2):
                continue
            por_tipo[tipo] += 1
            tomados += 1
            filas.append({
                "id": d.get("id", ""),
                "contenedor": contenedor.replace("silver-", ""),
                "titulo": (d.get("titulo", "") or "").replace("\n", " ").strip(),
                "descripcion": (d.get("descripcion", "") or "").replace("\n", " ").strip()[:500],
                "evento_reglas": tipo,
                "evento_modelo": "",   # se llena en la validación
                "evento_real": "",     # <<< la llena el humano
            })
        print(f"  {contenedor}: {tomados} registros ({dict(por_tipo)})")

    salida = "muestra_eventos.csv"
    with open(salida, "w", encoding="utf-8-sig", newline="") as f:
        campos = ["id", "contenedor", "titulo", "descripcion",
                  "evento_reglas", "evento_modelo", "evento_real"]
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(filas)

    print(f"\nListo: {len(filas)} registros en '{salida}'.")
    print("Ese archivo tiene el título y la descripción reales de cada registro,")
    print("la etiqueta de las reglas, y la columna 'evento_real' vacía.")
    print("\nSiguiente paso: mándame ese CSV para proponerte el 'evento_real' de cada uno.")


if __name__ == "__main__":
    main()