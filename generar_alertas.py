# -*- coding: utf-8 -*-
"""
generar_alertas.py
==================
Genera la tabla 'goldAlertasCentro' — el centro de inteligencia de la sección
Alertas. Reúne 4 tipos de alerta, todas RECIENTES y sin ruido, cada una con fecha:

  1. COMPETENCIA  -> contratos que ganó un competidor (de goldContratos, recientes)
  2. LICITACION   -> licitaciones abiertas que cierran pronto (de goldLicitaciones)
  3. REGULATORIO  -> cambios normativos recientes (de silver-regulatorio, filtrado)
  4. CLIENTE      -> señales de compra de clientes (de goldVentas)

Cada alerta: tipo, titulo, detalle, entidad, fecha, monto, dias, por_que_importa.

USO:  python generar_alertas.py            # simulacro
      python generar_alertas.py --escribir  # escribe la tabla
"""
from __future__ import annotations
import os, sys, json, time, hashlib, re, unicodedata
from datetime import datetime, timezone, timedelta

TABLA = "goldAlertasCentro"
DIAS_RECIENTE = 90   # ventana de recencia para competencia y regulatorio

def _norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()

def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"): return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No hay conexión"); sys.exit(1)


def _rk(tipo, clave):
    return hashlib.sha256(f"{tipo}|{clave}".encode("utf-8")).hexdigest()[:40]


def _dias_desde(fecha_str, futuro=False):
    """Días entre hoy y la fecha. futuro=True cuenta hacia adelante (para cierres)."""
    try:
        f = datetime.fromisoformat(str(fecha_str).replace("Z","+00:00")[:19])
        if f.tzinfo is None: f = f.replace(tzinfo=timezone.utc)
        d = (f - datetime.now(timezone.utc)).days if futuro else (datetime.now(timezone.utc) - f).days
        return d
    except Exception:
        return None


def alertas_competencia(tsc):
    """Contratos ganados por competidores, recientes (últimos 90 días)."""
    from azure.data.tables import TableServiceClient
    t = tsc.get_table_client("goldContratos")
    corte = (datetime.now(timezone.utc) - timedelta(days=DIAS_RECIENTE)).strftime("%Y-%m-%d")
    alertas = []
    for e in t.list_entities():
        if e.get("es_tak") == "Sí": continue
        ffirma = str(e.get("fecha_firma") or "")[:10]
        if not ffirma or ffirma < corte: continue
        try: valor = float(e.get("valor") or 0)
        except: valor = 0
        alertas.append({
            "tipo": "COMPETENCIA",
            "titulo": e.get("competidor","?"),
            "detalle": (e.get("objeto","") or "")[:160],
            "entidad": e.get("entidad_contratante","?"),
            "fecha": ffirma,
            "monto": valor,
            "categoria": e.get("categoria",""),
            "por_que": "Un competidor gan\u00f3 negocio en un servicio que TAK ofrece.",
            "clave": e.get("id_secop","") or e.get("RowKey",""),
        })
    alertas.sort(key=lambda x: x["fecha"], reverse=True)
    return alertas[:6]


def alertas_licitaciones(tsc):
    """Licitaciones abiertas que cierran pronto."""
    try:
        t = tsc.get_table_client("goldLicitaciones")
    except Exception:
        return []
    alertas = []
    for e in t.list_entities():
        cierre = str(e.get("fecha_cierre") or "")[:10]
        dias = _dias_desde(cierre, futuro=True)
        if dias is None or dias < 0: continue
        try: valor = float(e.get("valor") or 0)
        except: valor = 0
        alertas.append({
            "tipo": "LICITACION",
            "titulo": e.get("entidad","?"),
            "detalle": (e.get("objeto","") or "")[:160],
            "entidad": e.get("entidad","?"),
            "fecha": cierre,
            "monto": valor,
            "categoria": e.get("categoria",""),
            "dias": dias,
            "por_que": "Oportunidad abierta que TAK puede ganar antes del cierre.",
            "clave": e.get("RowKey",""),
        })
    alertas.sort(key=lambda x: x.get("dias", 999))
    return alertas[:10]


def alertas_regulatorio(bs):
    """Cambios normativos recientes y reales (sin ruido). La normativa tiene un
    ritmo más lento que las noticias, así que la ventana es más amplia (6 meses)."""
    cli = bs.get_container_client("silver-regulatorio")
    corte = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
    # DEBE ser normativa (ley/decreto/resolución...)
    NORMATIVO = ["ley", "decreto", "resolucion", "circular", "norma", "politica publica",
                 "regulacion", "regulatorio", "reglament", "proyecto de ley", "conpes"]
    # Y ADEMÁS debe tratar de un tema de TI EMPRESARIAL relevante para TAK
    TEMA_TI = ["datos", "data", "ciberseguridad", "seguridad de la informacion",
               "seguridad digital", "proteccion de datos", "habeas data", "soberania de datos",
               "transformacion digital", "inteligencia artificial", " ia ", "tecnologia",
               "software", "nube", "cloud", "contratacion publica", "secop", "compra publica",
               "interoperabilidad", "gobierno digital", "mintic", "tic", "computacion",
               "infraestructura tecnologica", "comercio electronico", "firma electronica",
               "factura electronica", "expediente electronico"]
    # ruido a excluir (aunque sea decreto, si es de estos temas NO va)
    RUIDO = ["award", "ranking", "premio", "puesto", "escala", "inversion de", "alianza",
             "sella", "financiamiento", "startup", "cupo",
             "ninos", "menores", "colegios", "escolar", "educacion basica",
             "redes sociales", "salud", "ambiental", "energia", "solar", "agua"]
    alertas = []
    for b in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        titulo = d.get("titulo","") or ""
        tl = _norm(titulo)
        fecha = str(d.get("fecha_evento_utc") or "")[:10]
        if not fecha or fecha < corte: continue           # solo recientes
        if any(x in tl for x in RUIDO): continue           # sin ruido
        if not any(x in tl for x in NORMATIVO): continue   # DEBE ser normativa
        if not any(x in tl for x in TEMA_TI): continue     # Y de tema TI empresarial
        alertas.append({
            "tipo": "REGULATORIO",
            "titulo": "Cambio normativo",
            "detalle": titulo[:160],
            "entidad": d.get("fuente_original",""),
            "fecha": fecha,
            "monto": 0,
            "categoria": "Regulatorio",
            "por_que": "Nuevas obligaciones pueden generar demanda de servicios TI.",
            "clave": d.get("id","") or titulo[:40],
        })
    alertas.sort(key=lambda x: x["fecha"], reverse=True)
    return alertas[:5]


def alertas_clientes(tsc):
    """Señales de compra de clientes (de goldVentas) — SOLO con fecha reciente."""
    try:
        t = tsc.get_table_client("goldVentas")
    except Exception:
        return []
    alertas = []
    for e in t.list_entities():
        senal = e.get("senal","") or ""
        fecha = str(e.get("fecha_senal","") or "")[:10]
        # SOLO las que tienen señal real Y fecha reciente
        if not senal or "sin noticia" in _norm(senal) or "sin actividad" in _norm(senal):
            continue
        if not fecha:   # sin fecha -> no es señal fresca -> fuera de alertas
            continue
        alertas.append({
            "tipo": "CLIENTE",
            "titulo": e.get("prospecto","?"),
            "detalle": senal[:160],
            "entidad": e.get("prospecto","?"),
            "fecha": fecha,
            "monto": 0,
            "categoria": e.get("servicio",""),
            "por_que": "Se\u00f1al de oportunidad de venta o captura.",
            "clave": e.get("RowKey",""),
        })
    # ordenar por fecha reciente
    alertas.sort(key=lambda x: x["fecha"], reverse=True)
    return alertas[:5]


def main():
    from azure.data.tables import TableServiceClient
    from azure.storage.blob import BlobServiceClient
    escribir = "--escribir" in sys.argv
    tsc = TableServiceClient.from_connection_string(_conn())
    bs = BlobServiceClient.from_connection_string(_conn())

    print("Generando centro de alertas...")
    comp = alertas_competencia(tsc)
    lic = alertas_licitaciones(tsc)
    reg = alertas_regulatorio(bs)
    cli = alertas_clientes(tsc)

    print(f"  Competencia (contratos recientes): {len(comp)}")
    print(f"  Licitaciones (cierran pronto):     {len(lic)}")
    print(f"  Regulatorio (normativo reciente):  {len(reg)}")
    print(f"  Clientes (señales):                {len(cli)}")

    todas = comp + lic + reg + cli
    orden = 0
    filas = []
    for a in todas:
        orden += 1
        filas.append({
            "PartitionKey": a["tipo"],
            "RowKey": _rk(a["tipo"], a["clave"]) + f"_{orden}",
            "orden": orden,
            "tipo": a["tipo"],
            "titulo": a["titulo"],
            "detalle": a["detalle"],
            "entidad": a["entidad"],
            "fecha": a["fecha"],
            "monto": float(a.get("monto") or 0),
            "dias": a.get("dias", -1),
            "categoria": a.get("categoria",""),
            "por_que": a["por_que"],
        })

    print(f"\n  TOTAL alertas: {len(filas)}")
    if reg:
        print("\n  Ejemplos regulatorio (recientes, sin ruido):")
        for a in reg[:3]:
            print(f"    [{a['fecha']}] {a['detalle'][:55]}")

    if not escribir:
        print("\n[SIMULACRO] No escribí la tabla. Corre con --escribir para crearla.")
        return

    from azure.data.tables import UpdateMode
    try:
        tsc.delete_table(TABLA); time.sleep(3)
    except Exception:
        pass
    dest = None
    for _ in range(12):
        try:
            dest = tsc.create_table_if_not_exists(TABLA); break
        except Exception as ex:
            if "BeingDeleted" in str(ex) or "Conflict" in str(ex): time.sleep(15)
            else: raise
    for f in filas:
        dest.upsert_entity(f, mode=UpdateMode.REPLACE)
    print(f"\nListo. Tabla '{TABLA}' con {len(filas)} alertas. Refresca Power BI.")


if __name__ == "__main__":
    main()