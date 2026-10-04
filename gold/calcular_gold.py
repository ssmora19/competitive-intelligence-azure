# -*- coding: utf-8 -*-
"""
calcular_gold.py
================

QUÉ HACE
--------
Es el orquestador de la capa Gold: el único archivo que se ejecuta para correr
todo. Valida la configuración, recorre los contenedores y, por cada uno, lee
Silver, calcula indicadores (agregación), veredictos y alertas (scoring), y lo
escribe en las tablas Gold. Al final genera la cobertura KIN una sola vez.

PARA QUÉ SIRVE
--------------
Amarra los cinco módulos (config, datos, agregación, scoring, cobertura) en un
solo flujo reproducible y auditable. Es el equivalente a clasificar_silver_gemini.py
pero para Gold. Deja evidencia de ejecución en executionLogsGold.

CÓMO SE EJECUTA
---------------
    python calcular_gold.py                 # corre los 8 contenedores
    python calcular_gold.py competidores    # corre solo uno (útil para probar)
    python calcular_gold.py --mensual        # granularidad mensual en vez de trimestral

DECISIONES DE DISEÑO (defendibles)
----------------------------------
- Valida la config ANTES de tocar Azure: si un peso no suma 1.0 o una señal de
  vencimiento no exige fecha_cierre, aborta con un mensaje claro. Falla temprano.
- Modo por contenedor: permite validar de a uno contra Azure antes de soltar los 8.
- Separación de capas intacta: este archivo NO calcula nada; solo coordina a los
  motores (que computan) y a gold_data (que escribe).
- Reproducible e idempotente: reejecutar sobreescribe (RowKeys deterministas), no
  duplica. Seguro de correr las veces que haga falta.
"""

from __future__ import annotations

import sys
import logging
from datetime import datetime, timezone

import config_gold as cfg
import gold_data as datos
import agregacion
import scoring
import cobertura_kin

log = logging.getLogger("gold.orquestador")


def _periodo_actual(granularidad: str) -> str:
    """QUÉ HACE: etiqueta el periodo en curso (para nombrar la corrida).
    PARA QUÉ SIRVE: dar un identificador de periodo a las salidas de estado."""
    hoy = datetime.now(timezone.utc)
    if granularidad == "mensual":
        return f"{hoy.year:04d}-{hoy.month:02d}"
    return f"{hoy.year:04d}-Q{(hoy.month - 1) // 3 + 1}"


def procesar_contenedor(contenedor: str, granularidad: str = "trimestral") -> dict:
    """
    QUÉ HACE
        Ejecuta el flujo Gold completo para UN contenedor: lee Silver, agrega,
        puntúa, genera alertas y escribe las tablas correspondientes.

    PARA QUÉ SIRVE
        Es la unidad de trabajo del orquestador. Aislarla permite correr un solo
        contenedor y depurarlo sin tocar los demás.

    DEVUELVE
        Resumen de lo procesado (para el log de ejecución).
    """
    log.info("=" * 60)
    log.info("CONTENEDOR: %s", contenedor)
    c = cfg.config(contenedor)
    silver_container = c["silver_container"]

    # 1. Leer Silver (solo relevantes)
    registros = datos.leer_silver(silver_container, solo_relevantes=True)
    if not registros:
        log.warning("  Sin registros relevantes en %s; se omite.", silver_container)
        return {"contenedor": contenedor, "registros": 0}

    # 1b. Limpiar filas viejas de este contenedor (evita zombis de corridas previas)
    datos.limpiar_contenedor(contenedor)

    # 2. Agregación → goldIndicadores
    agg = agregacion.calcular_indicadores(registros, contenedor, granularidad)
    n_ind = datos.guardar_indicadores_filas(contenedor, agg["filas"])

    # 3. Scoring → goldVeredictos
    veredictos = scoring.calcular_veredictos(registros, contenedor)
    periodo = _periodo_actual(granularidad)
    n_ver = datos.guardar_veredictos(contenedor, periodo, veredictos)

    # 4. Alertas de vencimiento → goldAlertas (solo si el contenedor las tiene)
    alertas = scoring.generar_alertas(registros, contenedor)
    n_alr = datos.guardar_alertas(contenedor, alertas) if alertas else 0

    resumen = {
        "contenedor": contenedor,
        "registros": len(registros),
        "filas_indicadores": n_ind,
        "veredictos": n_ver,
        "alertas": n_alr,
    }
    datos.registrar_log(contenedor, periodo, resumen)
    log.info("  → %d registros | %d indicadores | %d veredictos | %d alertas",
             len(registros), n_ind, n_ver, n_alr)
    return resumen


def ejecutar(contenedores: list[str] | None = None, granularidad: str = "trimestral") -> None:
    """
    QUÉ HACE
        Orquesta la corrida completa: valida la config, procesa los contenedores
        pedidos (todos por defecto) y genera la cobertura KIN.

    PARA QUÉ SIRVE
        Es el punto de entrada. Falla temprano si la config es incoherente, para
        no escribir datos malos en Azure.
    """
    # 0. Validar la configuración antes de tocar Azure
    avisos = cfg.validar_config()
    if avisos:
        log.error("La configuración tiene incoherencias; se aborta:")
        for a in avisos:
            log.error("   - %s", a)
        raise SystemExit(1)
    log.info("Config validada: coherente.")

    objetivo = contenedores or cfg.contenedores()
    resumenes = []
    for cont in objetivo:
        try:
            resumenes.append(procesar_contenedor(cont, granularidad))
        except Exception as e:  # noqa: BLE001
            log.exception("Error procesando '%s': %s", cont, e)
            resumenes.append({"contenedor": cont, "error": str(e)})

    # Cobertura KIN: se genera una sola vez, no por contenedor
    log.info("=" * 60)
    log.info("COBERTURA KIN")
    filas_cob = cobertura_kin.construir_filas()
    datos.guardar_cobertura(filas_cob)
    glob = cobertura_kin.resumen_global(filas_cob)
    log.info("  Cobertura global: %s%% (%d indicadores alimentables / %d fuera de alcance)",
             glob["cobertura_global_pct"], glob["indicadores_alimentables"],
             glob["indicadores_fuera_de_alcance"])

    # Resumen final
    log.info("=" * 60)
    log.info("GOLD COMPLETADO")
    for r in resumenes:
        if "error" in r:
            log.info("  %-22s ERROR: %s", r["contenedor"], r["error"])
        else:
            log.info("  %-22s reg=%-5d ind=%-3d ver=%-3d alr=%-3d",
                     r["contenedor"], r.get("registros", 0),
                     r.get("filas_indicadores", 0), r.get("veredictos", 0),
                     r.get("alertas", 0))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str]) -> tuple[list[str] | None, str]:
    """QUÉ HACE: interpreta los argumentos de línea de comandos.
    PARA QUÉ SIRVE: soportar 'todos', 'un contenedor' y '--mensual' sin librerías
    extra."""
    granularidad = "trimestral"
    contenedores: list[str] = []
    for arg in argv:
        if arg == "--mensual":
            granularidad = "mensual"
        elif arg == "--trimestral":
            granularidad = "trimestral"
        elif not arg.startswith("--"):
            contenedores.append(arg)
    return (contenedores or None), granularidad


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conts, gran = _parse_args(sys.argv[1:])
    if conts:
        for c in conts:
            if c not in cfg.contenedores():
                print(f"Contenedor desconocido: '{c}'. Válidos: {cfg.contenedores()}")
                raise SystemExit(2)
    ejecutar(conts, gran)