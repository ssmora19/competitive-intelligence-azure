# -*- coding: utf-8 -*-
"""
validacion.py
=============
Medición definitiva: compara REGLAS vs MODELO contra la verdad de terreno que
etiquetó el humano (columna evento_real de muestra_etiquetada.csv).

Corre el modelo sobre cada registro, y reporta:
  - Exactitud de las reglas   (evento_reglas vs evento_real)
  - Exactitud del modelo       (evento_modelo vs evento_real)
  - Falsos eventos de cada uno (marcó evento pero era 'otro_sin_evento')

Requisitos: pip install groq  + la API key en local.settings.json
Ejecuta:    python validacion.py
Lee:        muestra_etiquetada.csv   (el CSV con evento_real ya lleno)
Genera:     resultado_validacion.csv (con la columna evento_modelo añadida)
"""
from __future__ import annotations
import csv, time
import config_eventos as cfg
import clasificador

ENTRADA = "muestra_etiquetada.csv"
SALIDA = "resultado_validacion.csv"
EVENTOS = {"alianza", "lanzamiento", "expansion", "inversion",
           "contratacion_publica", "regulatorio", "actividad_tecnica"}


def main():
    with open(ENTRADA, encoding="utf-8-sig") as f:
        filas = list(csv.DictReader(f))
    print(f"Validando {len(filas)} registros con el modelo '{cfg.MOTOR}'...\n")

    motor = clasificador.obtener_motor()
    for i, r in enumerate(filas, 1):
        texto = f"{r['titulo']}. {r['descripcion']}".strip()[:cfg.MAX_CHARS_TEXTO]
        etiqueta, _ = motor.clasificar(texto)
        r["evento_modelo"] = etiqueta
        print(f"[{i}/{len(filas)}] reglas={r['evento_reglas']:20} "
              f"modelo={etiqueta:20} real={r['evento_real']}")
        if cfg.MOTOR == "groq":
            time.sleep(cfg.GROQ_PAUSA_SEG)

    with open(SALIDA, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=filas[0].keys()); w.writeheader(); w.writerows(filas)

    n = len(filas)
    ac_reglas = sum(1 for r in filas if r["evento_reglas"] == r["evento_real"])
    ac_modelo = sum(1 for r in filas if r["evento_modelo"] == r["evento_real"])
    fe_reglas = sum(1 for r in filas if r["evento_reglas"] in EVENTOS and r["evento_real"] == "otro_sin_evento")
    fe_modelo = sum(1 for r in filas if r["evento_modelo"] in EVENTOS and r["evento_real"] == "otro_sin_evento")

    print("\n" + "=" * 60)
    print(f"RESULTADO SOBRE {n} REGISTROS (verdad de terreno humana)")
    print("=" * 60)
    print(f"  REGLAS:  {ac_reglas}/{n} = {100*ac_reglas/n:.1f}% de exactitud")
    print(f"  MODELO:  {ac_modelo}/{n} = {100*ac_modelo/n:.1f}% de exactitud")
    print()
    print(f"  Falsos eventos (marcó evento, era ruido):")
    print(f"     reglas: {fe_reglas}    modelo: {fe_modelo}")
    print("=" * 60)
    ganador = "MODELO" if ac_modelo > ac_reglas else ("REGLAS" if ac_reglas > ac_modelo else "EMPATE")
    print(f"  Gana: {ganador}")
    print(f"\n  Guardado en {SALIDA}\n")


if __name__ == "__main__":
    main()