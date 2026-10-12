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
    """Días entre HOY (fecha de Colombia) y la fecha dada, contados por fecha y no
    por horas: una licitación que cierra mañana da 1 a cualquier hora del día.
    futuro=True cuenta hacia adelante (cierres); False cuenta hacia atrás (antigüedad)."""
    try:
        f = datetime.fromisoformat(str(fecha_str)[:10]).date()
        hoy = (datetime.now(timezone.utc) - timedelta(hours=5)).date()   # Colombia, UTC-5
        return (f - hoy).days if futuro else (hoy - f).days
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
            "url": e.get("url", "") or "",
            "clave": e.get("id_secop","") or e.get("RowKey",""),
        })
    alertas.sort(key=lambda x: x["fecha"], reverse=True)
    return alertas[:6]


# ─────────────────────────────────────────────────────────────
# RELACIÓN DE UNA ENTIDAD CON TAK (cliente propio / cliente de un competidor / mercado)
# Se cruza el nombre SECOP de la entidad con clientes.json y clientes_competencia.json.
# ─────────────────────────────────────────────────────────────
_relaciones = None
VENTANA_VENCE = 90          # días hacia adelante para "contratos por vencer"
PALABRAS_RENOVABLE = ("soporte", "renovacion", "renovar", "suscripcion", "licencia",
                      "mantenimiento", "servicio", "actualizacion", "garantia", "administracion")


def _cargar_relaciones():
    global _relaciones
    if _relaciones is not None:
        return _relaciones
    base = os.path.dirname(os.path.abspath(__file__))
    _relaciones = {}
    try:
        with open(os.path.join(base, "ingestion_clientes_competencia", "clientes_competencia.json"), encoding="utf-8") as f:
            for c in json.load(f):
                etiqueta = f"Cliente de {c.get('competidor', 'la competencia')}"
                for n in (c.get("nombre_secop"), c.get("entidad")):
                    if n:
                        _relaciones[_norm(n)] = etiqueta
    except Exception:
        pass
    try:
        with open(os.path.join(base, "ingestion_clientes", "clientes.json"), encoding="utf-8") as f:
            for c in json.load(f):          # un cliente propio prevalece sobre "cliente de"
                for n in (c.get("secop_nombres") or []) + [c.get("nombre")]:
                    if n:
                        _relaciones[_norm(n)] = "Cliente tuyo"
    except Exception:
        pass
    return _relaciones


def _relacion(entidad):
    """QUÉ HACE: dice si la entidad es cliente de TAK, cliente de un competidor o 'Mercado'."""
    rel = _cargar_relaciones()
    n = _norm(entidad)
    if not n:
        return "Mercado"
    if n in rel:
        return rel[n]
    for k, v in rel.items():                  # coincidencia parcial (nombres largos de SECOP)
        if len(k) >= 8 and (k in n or n in k):
            return v
    return "Mercado"


def alertas_vencimientos(tsc):
    """
    Contratos de TI que las entidades tienen CON COMPETIDORES y que terminan en los
    próximos VENTANA_VENCE días: la entidad tendrá que renovar o volver a contratar,
    y TAK puede acercarse ANTES de que salga la licitación.
    """
    t = tsc.get_table_client("goldContratos")
    hoy = (datetime.now(timezone.utc) - timedelta(hours=5)).date()   # fecha de Colombia
    alertas = []
    for e in t.list_entities():
        if e.get("es_tak") == "S\u00ed":
            continue
        ff = e.get("fecha_fin")
        if not ff:
            continue
        try:
            fin = ff.date() if hasattr(ff, "date") else datetime.fromisoformat(str(ff)[:10]).date()
        except ValueError:
            continue
        dias = (fin - hoy).days
        if dias < 0 or dias > VENTANA_VENCE:
            continue
        try: valor = float(e.get("valor") or 0)
        except: valor = 0
        objeto = (e.get("objeto", "") or "")
        renovable = any(p in _norm(objeto) for p in PALABRAS_RENOVABLE)
        entidad = e.get("entidad_contratante", "?")
        # Contrato modificado (prórroga o adición): SECOP ya trae la fecha de fin y el valor
        # ACTUALES; la página del PROCESO en SECOP sigue mostrando los iniciales. Se aclara en
        # la tarjeta para que nadie piense que el tablero tiene un dato equivocado.
        try:
            dias_ad = int(float(e.get("dias_adicionados") or 0))
        except (TypeError, ValueError):
            dias_ad = 0
        modificado = dias_ad > 0 or _norm(e.get("estado", "")) == "modificado"
        if dias_ad > 0:
            nota = (f"Contrato modificado: prorrogado {dias_ad} días. La fecha y el valor ya incluyen "
                    "las modificaciones; la página del proceso en SECOP muestra los valores iniciales.")
        elif modificado:
            nota = ("Contrato modificado: la fecha y el valor ya incluyen las modificaciones; "
                    "la página del proceso en SECOP muestra los valores iniciales.")
        else:
            nota = ""
        marcas = []
        if renovable:
            marcas.append("Renovable")
        if _relacion(entidad) == "Cliente tuyo":
            marcas.append("Cliente tuyo")
        if modificado:
            marcas.append("Prorrogado" if dias_ad > 0 else "Modificado")
        alertas.append({
            "tipo": "VENCIMIENTO",
            "titulo": entidad,
            "detalle": objeto[:160],
            "entidad": entidad,
            "fecha": fin.isoformat(),
            "monto": valor,
            "categoria": f"Hoy con {e.get('competidor', '?')}",
            "dias": dias,
            "por_que": " \u00b7 ".join(marcas),
            "url": e.get("url", "") or "",
            "clave": e.get("id_secop", "") or e.get("RowKey", ""),
            "_renovable": renovable,
            "nota": nota,
        })
    # primero los renovables, luego los que vencen antes, y a igual fecha los de mayor valor
    alertas.sort(key=lambda x: (not x["_renovable"], x["dias"], -x["monto"]))
    return alertas[:10]


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
            "por_que": _relacion(e.get("entidad", "")),   # Cliente tuyo / Cliente de X / Mercado
            "url": e.get("url", "") or "",                 # enlace al proceso en SECOP
            "clave": e.get("RowKey",""),
        })
    alertas.sort(key=lambda x: x.get("dias", 999))
    return alertas          # TODAS las licitaciones vigentes de TI, de la que cierra antes a la última


# ── Filtro regulatorio ────────────────────────────────────────────────
# Se comparan PALABRAS COMPLETAS (no pedazos): antes "tic" coincidía con
# "polí-TIC-a" y "ley" con "LEY-enda", y así se colaban columnas de opinión.
# Una noticia entra solo si cumple las TRES condiciones:
#   1) habla de un ACTO normativo concreto (decreto, resolución, ley, circular...)
#   2) trata un tema de TI empresarial relevante para TAK
#   3) no es opinión, premio, evento ni un tema ajeno (salud, educación, energía...)
REG_ACTO = [
    r"decretos?", r"resoluci[oó]n(es)?", r"leyes", r"ley", r"proyectos? de ley",
    r"circular(es)?", r"conpes", r"reglament\w*",
    r"regulaci[oó]n(es)?", r"regula", r"regular[aá]", r"normativ\w*", r"normas?",
    r"sanciona\w*", r"sanci[oó]n(es)?", r"multa\w*", r"obligatori\w*",
    r"entra(r[aá])? en vigencia", r"entra(r[aá])? en vigor", r"habeas data",
]
REG_TEMA = [
    r"datos", r"data", r"ciberseguridad", r"seguridad (de la informaci[oó]n|digital)",
    r"protecci[oó]n de datos", r"privacidad", r"habeas data", r"soberan[ií]a de datos",
    r"transformaci[oó]n digital", r"inteligencia artificial", r"ia", r"tecnolog[ií]as?",
    r"software", r"nube", r"cloud", r"contrataci[oó]n p[uú]blica", r"secop",
    r"compras? p[uú]blicas?", r"interoperabilidad", r"gobierno digital", r"mintic",
    r"tic", r"computaci[oó]n", r"infraestructura tecnol[oó]gica",
    r"comercio electr[oó]nico", r"firma (electr[oó]nica|digital)",
    r"factura(ci[oó]n)? electr[oó]nica", r"expediente electr[oó]nico", r"digital(es)?",
    r"ciberdelitos?", r"telecomunicaciones", r"sic", r"superindustria",
]
REG_RUIDO = [
    r"awards?", r"ranking", r"premios?", r"puestos?", r"escalaf[oó]n", r"inversi[oó]n de",
    r"alianzas?", r"sello", r"financiaci[oó]n", r"financiamiento", r"startups?", r"cupos?",
    r"ni[nñ]os", r"menores", r"colegios?", r"escolar(es)?", r"educaci[oó]n b[aá]sica",
    r"redes sociales", r"salud", r"ambiental(es)?", r"energ[ií]a", r"solar", r"agua",
    r"foro", r"evento", r"congreso", r"webinar", r"convocatoria", r"entrevista",
    r"opini[oó]n", r"columna", r"hay que", r"deber[ií]a(n)?", r"urge",
]


def _patron(lista):
    return re.compile(r"\b(" + "|".join(lista) + r")\b", re.IGNORECASE)


_RE_ACTO, _RE_TEMA, _RE_RUIDO = _patron(REG_ACTO), _patron(REG_TEMA), _patron(REG_RUIDO)


def clasificar_regulatorio(titulo: str) -> tuple[bool, str]:
    """QUÉ HACE: decide si un titular es un cambio normativo de TI. Devuelve
    (pasa, motivo); el motivo dice qué palabra lo hizo entrar o salir."""
    t = " ".join(str(titulo or "").replace("\u201c", '"').replace("\u201d", '"').split())
    if not t:
        return False, "sin título"
    if t[:1] in "\"'«":
        return False, "es una cita/opinión (empieza entre comillas)"
    r = _RE_RUIDO.search(t)
    if r:
        return False, f"tema ajeno u opinión: '{r.group(0)}'"
    a = _RE_ACTO.search(t)
    if not a:
        return False, "no menciona un acto normativo"
    m = _RE_TEMA.search(t)
    if not m:
        return False, f"normativo ('{a.group(0)}') pero no de TI"
    return True, f"acto '{a.group(0)}' + tema '{m.group(0)}'"


def alertas_regulatorio(bs, mostrar_rechazos: bool = False):
    """Cambios normativos recientes y reales (sin ruido). La normativa tiene un
    ritmo más lento que las noticias, así que la ventana es más amplia (6 meses).
    Si ninguna noticia cumple el filtro, la columna queda vacía: es preferible a
    mostrarle al gerente algo que no es regulación."""
    cli = bs.get_container_client("silver-regulatorio")
    corte = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
    alertas, rechazos = [], []
    vistos = set()
    for b in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        titulo = d.get("titulo", "") or ""
        fecha = str(d.get("fecha_evento_utc") or "")[:10]
        if not fecha or fecha < corte:
            continue                                        # solo recientes
        clave_tit = _norm(titulo)[:80]
        if clave_tit in vistos:
            continue                                        # misma noticia en dos fuentes
        vistos.add(clave_tit)
        pasa, motivo = clasificar_regulatorio(titulo)
        if not pasa:
            rechazos.append((fecha, titulo, motivo))
            continue
        alertas.append({
            "tipo": "REGULATORIO",
            "titulo": "Cambio normativo",
            "detalle": titulo[:160],
            "entidad": d.get("fuente_original", ""),
            "fecha": fecha,
            "monto": 0,
            "categoria": "Regulatorio",
            "por_que": "Nuevas obligaciones pueden generar demanda de servicios TI.",
            "url": d.get("url", "") or "",
            "clave": d.get("id", "") or titulo[:40],
            "motivo": motivo,
        })
    alertas.sort(key=lambda x: x["fecha"], reverse=True)
    if mostrar_rechazos:
        rechazos.sort(reverse=True)
        print(f"\n  Regulatorio: {len(alertas)} aceptadas, {len(rechazos)} descartadas (recientes).")
        print("  Últimas descartadas y por qué:")
        for f, t, mo in rechazos[:15]:
            print(f"    [{f}] {t[:70]}  -> {mo}")
    return alertas[:5]


def _por_que_cliente(e):
    """QUÉ HACE: arma la justificación visible de una señal de cliente:
    por qué se sugiere ese servicio y de qué noticia/fuente sale."""
    servicio = e.get("servicio", "") or "este servicio"
    razon = (e.get("por_que", "") or "").strip()
    fuente = (e.get("evidencia", "") or "").strip()
    fecha_f = str(e.get("evidencia_fecha", "") or "")[:10]
    partes = []
    if razon and servicio.lower().startswith("por identificar"):
        partes.append(f"Por qu\u00e9 sin servicio definido: {razon}")
    elif razon:
        partes.append(f"Por qu\u00e9 {servicio}: {razon}")
    if fuente:
        partes.append(f"Fuente: {fuente}" + (f" ({fecha_f})" if fecha_f else ""))
    return " | ".join(partes) or "Se\u00f1al de oportunidad de venta o captura."


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
        # Prospectos sin análisis real de IA (límite de Groq): no son una señal útil.
        if "actividad relevante" in _norm(senal) or "pendiente de analisis" in _norm(senal):
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
            "por_que": _por_que_cliente(e),
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
    reg = alertas_regulatorio(bs, mostrar_rechazos=not escribir)
    # Las señales de clientes viven en la sección Ventas: no se repiten aquí.
    venc = alertas_vencimientos(tsc)

    print(f"  Competencia (contratos recientes): {len(comp)}")
    print(f"  Licitaciones (cierran pronto):     {len(lic)}")
    print(f"  Regulatorio (normativo reciente):  {len(reg)}")
    print(f"  Contratos por vencer ({VENTANA_VENCE} días):   {len(venc)}")

    # La competencia (contratos ganados) vive en la sección Competidores: no se muestra
    # en el Centro de Alertas, pero se sigue guardando por si se necesita.
    todas = comp + lic + reg + venc
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
            "url": a.get("url", "") or "",
            "nota": a.get("nota", "") or "",
        })

    print(f"\n  TOTAL alertas: {len(filas)}")
    if reg:
        print("\n  Regulatorio que se mostrará (y por qué pasó el filtro):")
        for a in reg:
            print(f"    [{a['fecha']}] {a['detalle'][:70]}  -> {a['motivo']}")
    else:
        print("\n  Regulatorio: ninguna noticia reciente es un cambio normativo de TI (columna vacía).")

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