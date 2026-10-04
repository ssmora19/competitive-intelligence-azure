# -*- coding: utf-8 -*-
"""
cobertura_kin.py
================

QUÉ HACE
--------
Genera la tabla de cobertura KIN a partir de config_gold: por cada contenedor/KIN,
declara qué indicadores SÍ calcula el sistema (alimentables por OSINT/SOCMINT),
de qué fuente, y qué indicadores quedan FUERA DE ALCANCE con su motivo (interno /
HUMINT / financiero). Calcula además el porcentaje de cobertura por KIN y global.

PARA QUÉ SIRVE
--------------
Es la evidencia consultable de trazabilidad para el jurado. Materializa, como
filas en la tabla goldCoberturaKIN, todo el inventario que se validó: responde de
forma auditable "¿qué preguntas de inteligencia responde el sistema y cuáles no, y
por qué". Convierte una decisión de alcance (que el jurado podría leer como
carencia) en una declaración explícita y defendible.

DECISIÓN DE DISEÑO (defendible)
-------------------------------
La cobertura NO se escribe a mano: se DERIVA de la config. Así, si cambia la
parametrización (se agrega un indicador, se declara otro fuera de alcance), la
cobertura se recalcula sola y nunca queda desactualizada respecto al sistema real.
Es una sola fuente de verdad.

FUENTE POR CONTENEDOR
---------------------
Se mapea cada contenedor a la fuente OSINT/SOCMINT que realmente lo alimenta, para
que la tabla muestre la procedencia de cada respuesta (Bronze → Silver → Gold).
"""

from __future__ import annotations

import logging

import config_gold as cfg

log = logging.getLogger("gold.cobertura")

# Fuente principal que alimenta cada contenedor (de dónde salen sus señales).
FUENTES = {
    "competidores":         "Web, noticias (Google News RSS), redes (Apify/LinkedIn)",
    "clientes":             "SECOP (Socrata), LinkedIn Jobs (Apify)",
    "clientes-competencia": "SECOP (Socrata), Google News RSS",
    "tendencias":           "GitHub API, RSS/feeds, redes",
    "proveedores":          "Web fabricantes (Firecrawl), noticias",
    "mercado":              "SECOP (Socrata)",
    "regulatorio":          "MinTIC, Colombia Compra, normativa (web)",
    "capacidades":          "LinkedIn Jobs (Apify), benchmarking de competidores",
}


def construir_filas() -> list[dict]:
    """
    QUÉ HACE
        Arma, por cada contenedor, la fila de cobertura KIN con sus indicadores
        alimentables, su fuente, lo fuera de alcance y el % de cobertura.

    PARA QUÉ SIRVE
        Producir la estructura lista para gold_data.guardar_cobertura(). Cada fila
        es un KIN con su estado de cobertura, consultable por el dashboard/jurado.

    DEVUELVE
        Lista de dicts: {kin_id, kin_nombre, contenedor, alimentable, fuente,
        indicadores_asociados, fuera_de_alcance, cobertura_pct}.
    """
    filas = []
    for contenedor in cfg.contenedores():
        c = cfg.config(contenedor)
        alimentables = cfg.indicadores_alimentables(contenedor)
        fuera = c.get("fuera_de_alcance", {})

        # Descripción legible de cada indicador alimentable
        inds_desc = [ind.get("desc", nombre) for nombre, ind in alimentables.items()]

        n_alim, n_fuera = len(alimentables), len(fuera)
        total = n_alim + n_fuera
        cobertura_pct = round(100 * n_alim / total, 1) if total else 0.0

        # Estado de alimentabilidad del KIN
        if n_fuera == 0:
            estado = "completa"
        elif n_alim == 0:
            estado = "fuera_de_alcance"
        else:
            estado = "parcial"

        filas.append({
            "kin_id": c["kin_id"],
            "kin_nombre": c.get("kin_nombre", ""),
            "contenedor": contenedor,
            "alimentable": estado,
            "fuente": FUENTES.get(contenedor, ""),
            "indicadores_asociados": inds_desc,
            "fuera_de_alcance": fuera,
            "cobertura_pct": cobertura_pct,
        })
    return filas


def resumen_global(filas: list[dict]) -> dict:
    """
    QUÉ HACE: agrega el total de indicadores alimentables vs fuera de alcance y el
    porcentaje de cobertura global del sistema.
    PARA QUÉ SIRVE: es el número que resume la cobertura del sistema ante el jurado
    ("el sistema responde el 71% de los indicadores por fuentes abiertas").
    """
    total_alim = sum(len(f["indicadores_asociados"]) for f in filas)
    total_fuera = sum(len(f["fuera_de_alcance"]) for f in filas)
    total = total_alim + total_fuera
    completas = sum(1 for f in filas if f["alimentable"] == "completa")
    parciales = sum(1 for f in filas if f["alimentable"] == "parcial")
    fuera = sum(1 for f in filas if f["alimentable"] == "fuera_de_alcance")
    return {
        "kins_totales": len(filas),
        "kins_cobertura_completa": completas,
        "kins_cobertura_parcial": parciales,
        "kins_fuera_de_alcance": fuera,
        "indicadores_alimentables": total_alim,
        "indicadores_fuera_de_alcance": total_fuera,
        "cobertura_global_pct": round(100 * total_alim / total, 1) if total else 0.0,
    }


# ===========================================================================
# PRUEBA AUTÓNOMA (corre sin Azure)
#   Ejecuta:  python cobertura_kin.py
# ===========================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    filas = construir_filas()

    print("\n=== TABLA DE COBERTURA KIN ===\n")
    for f in filas:
        marca = {"completa": "[COMPLETA]", "parcial": "[PARCIAL ]",
                 "fuera_de_alcance": "[FUERA   ]"}[f["alimentable"]]
        print(f"{marca} {f['kin_id']:7} {f['contenedor']:22} cobertura={f['cobertura_pct']:>5}%")
        print(f"          fuente: {f['fuente']}")
        print(f"          responde: {len(f['indicadores_asociados'])} indicador(es)")
        for d in f["indicadores_asociados"]:
            print(f"             + {d}")
        if f["fuera_de_alcance"]:
            print(f"          fuera de alcance:")
            for k, motivo in f["fuera_de_alcance"].items():
                print(f"             - {k}: {motivo}")
        print()

    print("=== RESUMEN GLOBAL (para el jurado) ===\n")
    r = resumen_global(filas)
    for k, v in r.items():
        print(f"   {k:32} {v}")
    print()