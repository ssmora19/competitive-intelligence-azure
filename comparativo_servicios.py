# -*- coding: utf-8 -*-
"""
comparativo_servicios.py  (v2 — usa la categoría ya calculada en goldContratos)
===============================================================================
Agrupa los contratos de goldContratos por CATEGORÍA (la que ya trae cada
contrato, específica) y compara TAK vs competidores en cada una.
Escribe la tabla 'goldComparativoServicios' para el tablero.

Responde al pedido de Ana María: "comparar lo que ha ganado TAK en tal servicio
vs lo que han ganado los competidores en ese mismo servicio."

USO:  python comparativo_servicios.py            # simulacro
      python comparativo_servicios.py --escribir  # escribe la tabla
"""
from __future__ import annotations
import os, sys, json, re, time
from collections import defaultdict

TABLA_ORIGEN = "goldContratos"
TABLA_DESTINO = "goldComparativoServicios"


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré la conexión."); sys.exit(1)


# ¿TAK ofrece esta categoría? (según portafolio oficial de takcolombia.com)
# Sí = servicio claro del portafolio | Parcial = implícito | No = fuera del portafolio
PORTAFOLIO_TAK = {
    "Servicios en la nube": "Sí",
    "Licenciamiento de software": "Sí",       # Licencias Oracle Linux
    "Bases de datos": "Sí",
    "Ciberseguridad": "Sí",
    "Consultoría TI": "Sí",
    "Implementación de sistemas": "Sí",        # Infraestructura, Capa Media
    "Soporte de software": "Sí",               # Virtualización y SO
    "Mantenimiento de sistemas": "Sí",         # Virtualización, Infraestructura
    "Modernización de sistemas": "Sí",         # Infraestructura
    "Hardware": "Sí",
    "Interventoría / auditoría TI": "Sí",      # servicio profesional TI
    "Software especializado": "Parcial",       # depende del software
    "Respaldo y recuperación": "Parcial",      # implícito en Infraestructura
    "Datos e internet": "Parcial",
    "Software": "Parcial",
    "Redes": "No",                             # no está en el portafolio
    "Soporte de redes": "No",
    "Telecom / portales": "No",
    "Mantenimiento de equipos": "Parcial",     # relacionado con Hardware
    "Otros TI": "Parcial",
    "Servicios TI": "Sí",
}


def _tak_ofrece(categoria):
    return PORTAFOLIO_TAK.get(categoria, "Parcial")


def main():
    from azure.data.tables import TableServiceClient, UpdateMode
    escribir = "--escribir" in sys.argv
    tsc = TableServiceClient.from_connection_string(_conn())
    tabla = tsc.get_table_client(TABLA_ORIGEN)

    # categoria -> {tak_valor, tak_n, comp_valor, comp_n}
    datos = defaultdict(lambda: {"tak_valor": 0.0, "tak_n": 0, "comp_valor": 0.0, "comp_n": 0})

    for e in tabla.list_entities():
        cat = e.get("categoria") or "Otros TI"
        try:
            valor = float(e.get("valor") or 0)
        except (TypeError, ValueError):
            valor = 0
        if e.get("es_tak") == "Sí":
            datos[cat]["tak_valor"] += valor
            datos[cat]["tak_n"] += 1
        else:
            datos[cat]["comp_valor"] += valor
            datos[cat]["comp_n"] += 1

    print("=" * 78)
    print("COMPARATIVO TAK vs COMPETIDORES POR SERVICIO")
    print("=" * 78)
    print(f"  {'categoría':30} {'TAK $':>15} {'Comp $':>15} {'#TAK':>5} {'#Comp':>6} {'TAK%':>6}")
    print("  " + "-" * 74)
    filas = []
    orden = 0
    for cat, d in sorted(datos.items(), key=lambda x: -(x[1]["tak_valor"]+x[1]["comp_valor"])):
        total = d["tak_valor"] + d["comp_valor"]
        pct_tak = 100*d["tak_valor"]/total if total else 0
        ofrece = _tak_ofrece(cat)
        print(f"  {cat[:28]:28} {d['tak_valor']:>14,.0f} {d['comp_valor']:>14,.0f} {pct_tak:>5.1f}%  TAK ofrece: {ofrece}")
        orden += 1
        filas.append({
            "PartitionKey": "servicio",
            "RowKey": re.sub(r"[^a-zA-Z0-9]", "_", cat)[:60],
            "orden": orden,
            "categoria": cat,
            "tak_valor": d["tak_valor"],
            "tak_contratos": d["tak_n"],
            "comp_valor": d["comp_valor"],
            "comp_contratos": d["comp_n"],
            "tak_pct": round(pct_tak, 1),
            "tak_ofrece": _tak_ofrece(cat),
        })

    if not escribir:
        print("\n[SIMULACRO] No escribí la tabla. Corre con --escribir para crearla.")
        return

    try:
        tsc.delete_table(TABLA_DESTINO); time.sleep(3)
    except Exception:
        pass
    dest = None
    for _ in range(12):
        try:
            dest = tsc.create_table_if_not_exists(TABLA_DESTINO); break
        except Exception as ex:
            if "BeingDeleted" in str(ex) or "Conflict" in str(ex):
                time.sleep(15)
            else:
                raise
    for f in filas:
        dest.upsert_entity(f, mode=UpdateMode.REPLACE)
    print(f"\nListo. Tabla '{TABLA_DESTINO}' con {len(filas)} categorías. Refresca Power BI.")


if __name__ == "__main__":
    main()