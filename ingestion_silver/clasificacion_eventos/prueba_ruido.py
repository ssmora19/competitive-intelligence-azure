# -*- coding: utf-8 -*-
"""
prueba_ruido.py
===============
Prueba puntual: clasifica con el modelo unas noticias que NO pasaron el umbral de
relevancia (el "cajón noticia" que las reglas descartaron). Sirve para decidir:
  - Si el modelo también dice 'otro_sin_evento' → las reglas ya bastan (Hipótesis A).
  - Si el modelo encuentra alianzas/lanzamientos reales → hay eventos escondidos
    que las reglas botaron (Hipótesis B), y vale la pena reclasificar.

Ejecuta:  python prueba_ruido.py silver-clientes-competencia 10
"""
from __future__ import annotations
import os, sys, json, time
import config_eventos as cfg
import clasificador


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
    contenedor = sys.argv[1] if len(sys.argv) > 1 else "silver-clientes-competencia"
    cuantas = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 10

    from azure.storage.blob import BlobServiceClient
    cli = BlobServiceClient.from_connection_string(_conn()).get_container_client(contenedor)

    print(f"\nBuscando noticias NO relevantes (el 'ruido') en '{contenedor}'...\n")
    motor = clasificador.obtener_motor()

    revisadas = 0
    rescatadas = 0
    print("=" * 72)
    for blob in cli.list_blobs():
        if revisadas >= cuantas:
            break
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        clasif = d.get("clasificacion", {}) or {}
        relev = d.get("relevancia", {}) or {}
        # solo las que son 'noticia' Y NO pasaron el umbral (el ruido descartado)
        if clasif.get("tipo_senal") != "noticia" or relev.get("pasa_umbral") is True:
            continue
        titulo = d.get("titulo", "") or ""
        desc = d.get("descripcion", "") or ""
        texto = f"{titulo}. {desc}".strip()[:cfg.MAX_CHARS_TEXTO]
        etiqueta, conf = motor.clasificar(texto)
        revisadas += 1
        marca = ""
        if etiqueta != "otro_sin_evento":
            rescatadas += 1
            marca = "  <<< EVENTO RESCATADO"
        print(f"\n[{revisadas}] {titulo[:70]}")
        print(f"    Reglas: descartada (ruido) | Modelo: '{etiqueta}'{marca}")
        if cfg.MOTOR == "groq":
            time.sleep(cfg.GROQ_PAUSA_SEG)

    print("\n" + "=" * 72)
    print(f"\nDe {revisadas} noticias que las reglas DESCARTARON como ruido,")
    print(f"el modelo encontró {rescatadas} con un evento de negocio real.")
    if revisadas:
        pct = 100 * rescatadas / revisadas
        print(f"→ {pct:.0f}% de rescate.")
        if pct < 15:
            print("  Interpretación: las reglas ya bastan (Hipótesis A). Reclasificar aporta poco.")
        else:
            print("  Interpretación: hay eventos escondidos (Hipótesis B). Vale la pena reclasificar.")
    print()


if __name__ == "__main__":
    main()