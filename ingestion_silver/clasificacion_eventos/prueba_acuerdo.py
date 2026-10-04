# -*- coding: utf-8 -*-
"""
prueba_acuerdo.py
=================
Prueba lo que de verdad le importa a Gold: ¿las reglas clasificaron BIEN las
noticias que marcaron con un evento específico (alianza, lanzamiento, inversion…)?

Toma noticias RELEVANTES cuyo tipo_senal ya es un evento concreto (no 'noticia'
genérica) y le pregunta al modelo si está de acuerdo con esa etiqueta. Reporta el
% de acuerdo. Un acuerdo alto = las reglas segmentan bien = Gold contará bien.
Un acuerdo bajo = las reglas confunden tipos = Gold necesitaría el modelo.

Ejecuta:  python prueba_acuerdo.py silver-competidores 20
"""
from __future__ import annotations
import os, sys, json, time
from collections import Counter
import config_eventos as cfg
import clasificador

# Tipos de EVENTO concreto que queremos verificar (no 'noticia', que es genérico).
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
    contenedor = sys.argv[1] if len(sys.argv) > 1 else "silver-competidores"
    cuantas = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 20

    from azure.storage.blob import BlobServiceClient
    cli = BlobServiceClient.from_connection_string(_conn()).get_container_client(contenedor)
    motor = clasificador.obtener_motor()

    print(f"\nVerificando si las reglas segmentaron BIEN los eventos en '{contenedor}'...")
    print("(comparando la etiqueta de las reglas contra el criterio del modelo)\n")
    print("=" * 74)

    revisadas = 0
    de_acuerdo = 0
    desacuerdos = Counter()  # (regla → modelo) para ver dónde difieren
    for blob in cli.list_blobs():
        if revisadas >= cuantas:
            break
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        clasif = d.get("clasificacion", {}) or {}
        relev = d.get("relevancia", {}) or {}
        tipo_reglas = clasif.get("tipo_senal")
        # solo las relevantes con un evento concreto (lo que Gold va a contar)
        if tipo_reglas not in EVENTOS or relev.get("pasa_umbral") is not True:
            continue
        titulo = d.get("titulo", "") or ""
        desc = d.get("descripcion", "") or ""
        texto = f"{titulo}. {desc}".strip()[:cfg.MAX_CHARS_TEXTO]
        etiqueta_modelo, _ = motor.clasificar(texto)
        revisadas += 1
        coincide = (etiqueta_modelo == tipo_reglas)
        if coincide:
            de_acuerdo += 1
            marca = "OK"
        else:
            desacuerdos[f"{tipo_reglas} -> {etiqueta_modelo}"] += 1
            marca = "DIFIERE"
        print(f"\n[{revisadas}] {titulo[:66]}")
        print(f"    Reglas: '{tipo_reglas}'  |  Modelo: '{etiqueta_modelo}'  [{marca}]")
        if cfg.MOTOR == "groq":
            time.sleep(cfg.GROQ_PAUSA_SEG)

    print("\n" + "=" * 74)
    if revisadas == 0:
        print("\nNo encontré noticias relevantes con evento concreto en este contenedor.\n")
        return
    pct = 100 * de_acuerdo / revisadas
    print(f"\nDe {revisadas} eventos que las reglas clasificaron, el modelo coincidió "
          f"en {de_acuerdo} ({pct:.0f}% de acuerdo).")
    if desacuerdos:
        print("\nDónde difieren (regla -> modelo):")
        for k, n in desacuerdos.most_common():
            print(f"    {n:3}x  {k}")
    print()
    if pct >= 70:
        print("→ Acuerdo alto: las reglas segmentan bien. Gold contará bien con lo que hay.")
    elif pct >= 50:
        print("→ Acuerdo medio: las reglas aciertan a menudo pero hay confusión entre tipos.")
    else:
        print("→ Acuerdo bajo: las reglas confunden tipos. Gold necesitaría el modelo para segmentar bien.")
    print()


if __name__ == "__main__":
    main()