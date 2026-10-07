# -*- coding: utf-8 -*-
"""
generar_ventas.py  (v3 — con FECHA de la señal)
================================================
Genera la inteligencia de VENTAS para el comercial.

NOVEDAD v3: cada prospecto ahora guarda la FECHA de la noticia/actividad que
originó su señal (campo fecha_senal), para que las alertas muestren cuándo se
detectó — resolviendo el Tema 1 del cliente (fechas en todo).

Tipos de prospecto:
  - Lead     = cliente propio interesado (de clientes.json)
  - Captura  = cliente de la competencia (de clientes_competencia.json)

USO:  python generar_ventas.py            # simulacro
      python generar_ventas.py --escribir  # escribe
      python generar_ventas.py 5            # probar 5
"""
from __future__ import annotations
import os, sys, json, time, re, unicodedata

TABLA_VEREDICTOS = "goldVeredictos"
TABLA_VENTAS = "goldVentas"
MODELO_GROQ = "qwen/qwen3.8-27b"
PAUSA_SEG = 2.1
# Ahorro de cupo de Groq: un prospecto solo se vuelve a analizar si es nuevo, si cambió su
# evidencia o si su análisis tiene más de DIAS_VIGENCIA_ANALISIS días; y como máximo
# MAX_ANALISIS_POR_CORRIDA por corrida (el resto conserva su análisis anterior).
MAX_ANALISIS_POR_CORRIDA = 30
DIAS_VIGENCIA_ANALISIS = 7
CLAVE_GROQ_EN_SETTINGS = "GROQ_API_KEY"

TIPOS_VENTA = {"lead_score", "oportunidad_captura"}

CONT_NOTICIA = ["silver-competidores", "silver-clientes", "silver-clientes-competencia"]
FUENTES_NOTICIA = {"noticias_competidor", "noticias_cliente", "noticias_competencia"}
FUENTES_SECOP = {"secop_cliente_procesos", "secop_cliente_contratos",
                 "secop_cliente_adjudicatario", "secop_competencia_procesos",
                 "secop_competencia_contratos"}

_cache_not = None
_cache_secop = None
_competidores = None


def _norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).strip()


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


def _groq_key():
    env = os.environ.get(CLAVE_GROQ_EN_SETTINGS)
    if env: return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get(CLAVE_GROQ_EN_SETTINGS):
                return v[CLAVE_GROQ_EN_SETTINGS]
    print(f"No encontré '{CLAVE_GROQ_EN_SETTINGS}'."); sys.exit(1)


def _cargar_competidores():
    global _competidores
    if _competidores is not None: return _competidores
    for p in ("data/companies.json", "companies.json", os.path.join("..", "data", "companies.json")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                _competidores = [_norm(c["nombre"]) for c in json.load(f)]
            return _competidores
    _competidores = []
    return _competidores


def _es_competidor(nombre):
    n = _norm(nombre)
    return any(c in n or n in c for c in _cargar_competidores() if len(c) > 3)


def _es_ruido_noticia(titulo: str) -> bool:
    """True si la noticia NO es relevante para una señal de negocio TI
    (tragedias, hechos militares, política, deportes, farándula, etc.)."""
    t = _norm(titulo)
    RUIDO = [
        "muerte", "muerto", "murio", "fallecio", "asesinat", "homicidi", "masacre",
        "herido", "herida", "ataque armado", "combate", "soldado", "militar muere",
        "drone", "dron ", "secuestr", "captur", "detenid", "narcotrafico", "narco",
        "accidente", "incendio", "inundacion", "sismo", "terremoto", "desastre",
        "futbol", "seleccion colombia", "partido", "gol ", "deportes",
        "farandula", "escandalo", "polemica", "renuncia", "destitu",
        "elecciones", "candidato", "campaña politica", "protesta", "paro ",
        "violencia", "victima", "tragedia", "luto",
    ]
    return any(x in t for x in RUIDO)


def _cargar_silver():
    global _cache_not, _cache_secop
    if _cache_not is not None: return
    from azure.storage.blob import BlobServiceClient
    bs = BlobServiceClient.from_connection_string(_conn())
    _cache_not, _cache_secop = {}, {}
    for cont in CONT_NOTICIA:
        try:
            cli = bs.get_container_client(cont)
            blobs = list(cli.list_blobs())
        except Exception:
            continue
        for b in blobs:
            try:
                d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
            except Exception:
                continue
            fuente = d.get("fuente_original", "")
            ent = _norm(re.sub(r"\(cliente de.*?\)", "", d.get("entidad_monitoreada") or d.get("entidad_declarada") or "", flags=re.IGNORECASE))
            if not ent: continue
            if fuente in FUENTES_NOTICIA:
                tit = (d.get("titulo") or "").strip()
                fecha = str(d.get("fecha_evento_utc") or "")[:10]
                # excluir noticias que NO son de negocio/TI (tragedias, política, etc.)
                if tit and not _es_ruido_noticia(tit):
                    _cache_not.setdefault(ent, []).append({"titulo": tit, "fecha": fecha,
                                                           "url": d.get("url", "") or ""})
            elif fuente in FUENTES_SECOP:
                # Se guarda QUÉ contrata (objeto, valor, fecha), no solo cuántos procesos tiene,
                # para que la IA pueda fundamentar el servicio en lo que la entidad compra.
                bloque = d.get("bloque") or {}
                objeto = (bloque.get("objeto") or d.get("titulo") or "").strip()
                _cache_secop.setdefault(ent, []).append({
                    "titulo": objeto,
                    # solo la fecha de PUBLICACIÓN de un proceso es fecha de señal; la de un
                    # contrato es su fecha de fin, así que no se usa como fecha de la señal
                    "fecha": str(bloque.get("fecha_publicacion") or "")[:10],
                    "url": d.get("url", "") or "",
                    "valor": bloque.get("valor") or 0,
                })


def _noticias_de(nombre):
    """Devuelve las noticias RECIENTES (últimos 12 meses) del prospecto,
    más recientes primero. Descarta noticias viejas que no son señales vigentes."""
    from datetime import datetime, timezone, timedelta
    _cargar_silver()
    corte = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    obj = _norm(nombre)
    for ent, lst in _cache_not.items():
        if obj and (obj in ent or ent in obj):
            # solo noticias recientes (con fecha >= corte)
            recientes = [n for n in lst if (n.get("fecha","") or "") >= corte]
            return sorted(recientes, key=lambda x: x.get("fecha","") or "", reverse=True)[:4]
    return []


def _secop_items_de(nombre):
    """Procesos/contratos SECOP de la entidad (con objeto), los más recientes primero."""
    _cargar_silver()
    obj = _norm(nombre)
    items = []
    for ent, lst in _cache_secop.items():
        if obj and (obj in ent or ent in obj):
            items.extend(x for x in lst if x.get("titulo"))
    items.sort(key=lambda x: (x.get("fecha") or "", x.get("valor") or 0), reverse=True)
    return items


def _competidor_de(nombre_completo):
    m = re.search(r"cliente de\s+(.+?)\)", nombre_completo or "", flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


SYSTEM = (
    "Eres asesor comercial de TAK, empresa de TI colombiana. Su portafolio OFICIAL es: "
    "Inteligencia Artificial, Machine Learning, Ciberseguridad, Bases de datos (fuerte en Oracle), "
    "Business Intelligence, Infraestructura, Virtualización y sistemas operativos, Capa media "
    "(servidores de aplicaciones), Desarrollo de aplicaciones, Licencias, Hardware, Blockchain "
    "y Servicios en la nube (vía AWS y Microsoft). Para el prospecto que te doy, responde SOLO un "
    "JSON válido con estos campos: "
    '{"servicio":"<UNO del portafolio oficial, o Por identificar>",'
    '"nivel":"<Alto o Medio>",'
    '"senal":"<el HECHO real detectado del prospecto, máx 14 palabras, sin recomendar nada, '
    'solo qué está pasando. Ej: Ampliaron su red de infraestructura>",'
    '"recomendacion":"<la ACCIÓN que TAK debe tomar, máx 16 palabras, qué ofrecer y por qué. '
    'Ej: Ofrécele nube escalable para soportar ese crecimiento>",'
    '"por_que":"<máx 25 palabras: qué dice EXACTAMENTE la evidencia que justifica ese servicio. '
    'Ej: Tiene un contrato SECOP de soporte a bases de datos Oracle; eso es Bases de datos. '
    'No menciones números de evidencia: describe el hecho>",'
    '"evidencia":<el NÚMERO de la evidencia (noticia o SECOP) en la que te basas, '
    'o 0 si no hay ninguna>}. '
    "La SEÑAL sale de la noticia más relevante que te doy (el hecho puro). Si no hay noticia "
    "pero es muy activo en contratación pública (SECOP), la señal es eso (ej: Muy activo en "
    "licitaciones públicas). La RECOMENDACIÓN conecta ese hecho con un servicio de TAK. "
    "Varía los verbos. No inventes hechos falsos. IMPORTANTE: si la noticia NO se relaciona "
    "con una necesidad tecnológica real (ej: es sobre un hecho militar, una tragedia, política "
    "o deportes), NO fuerces una recomendación de TI rebuscada. En ese caso pon en senal una "
    "descripción NEUTRA de la entidad y una recomendación general de acercamiento comercial, "
    "sin inventar conexiones forzadas con ciberseguridad o nube. "
    "REGLA CLAVE: solo pon un servicio ESPECÍFICO cuando la evidencia lo justifique de forma "
    "explícita, y explica esa justificación en por_que. Si no hay evidencia clara de QUÉ "
    "necesita, el servicio es 'Por identificar' y en por_que dilo con honestidad (ej: La noticia "
    "no indica una necesidad tecnológica concreta). Nunca inventes la necesidad."
)


def _evidencias(noticias, secop):
    """Lista única y numerada de evidencias: hasta 4 noticias y hasta 4 objetos de SECOP."""
    ev = [dict(n, origen="noticia") for n in noticias[:4]]
    ev += [dict(x, origen="secop") for x in secop[:4]]
    return ev


def _prompt(nombre, tipo, score, evidencias, n_secop):
    tipoTxt = "cliente propio interesado" if tipo == "lead_score" else "cliente de la competencia capturable"
    if evidencias:
        lineas = []
        for i, e in enumerate(evidencias, 1):
            if e["origen"] == "noticia":
                lineas.append(f"  {i}. [Noticia, {e.get('fecha') or 'sin fecha'}] {e['titulo']}")
            else:
                valor = f", ${float(e.get('valor') or 0):,.0f}" if e.get("valor") else ""
                lineas.append(f"  {i}. [SECOP, lo que contrata{valor}] {e['titulo'][:200]}")
        contexto = (f"Evidencias numeradas ({n_secop} procesos/contratos SECOP en total):\n"
                    + "\n".join(lineas) +
                    "\nLas de SECOP dicen QUÉ compra la entidad: úsalas para fundamentar el servicio "
                    "si se relacionan con el portafolio de TAK. Si lo que compra no es de TI "
                    "(obras, alimentos, aseo, etc.), no fuerces un servicio: pon 'Por identificar'.")
    else:
        contexto = "Sin noticias ni actividad SECOP relevante."
    return (
        f"Prospecto: {nombre}\nTipo: {tipoTxt}\nPrioridad (0-1): {score}\n{contexto}\n\n"
        f"Genera el JSON con servicio, nivel, senal, recomendacion, por_que y evidencia."
    )


# Normaliza el nombre de servicio que genera la IA a la lista canónica del
# portafolio de TAK (evita duplicados por mayúsculas/sinónimos).
SERVICIOS_CANONICOS = {
    "nube": "Servicios en la nube",
    "servicios en la nube": "Servicios en la nube",
    "cloud": "Servicios en la nube",
    "computacion en la nube": "Servicios en la nube",
    "ciberseguridad": "Ciberseguridad",
    "seguridad": "Ciberseguridad",
    "seguridad informatica": "Ciberseguridad",
    "business intelligence": "Business Intelligence",
    "bi": "Business Intelligence",
    "inteligencia de negocios": "Business Intelligence",
    "inteligencia artificial": "Inteligencia Artificial",
    "ia": "Inteligencia Artificial",
    "machine learning": "Machine Learning",
    "bases de datos": "Bases de datos",
    "bases de datos oracle": "Bases de datos",
    "base de datos": "Bases de datos",
    "oracle": "Bases de datos",
    "infraestructura": "Infraestructura",
    "infraestructura ti": "Infraestructura",
    "infraestructura tecnologica": "Infraestructura",
    "blockchain": "Blockchain",
    "desarrollo": "Desarrollo de software",
    "desarrollo de software": "Desarrollo de software",
    "por identificar": "Por identificar",
}


# Texto que marca a un prospecto sin análisis de IA (no se muestra como alerta).
TEXTO_PENDIENTE = "Pendiente de análisis (se completa en la próxima corrida)."


def _es_relleno(senal: str) -> bool:
    """QUÉ HACE: detecta señales que no son un análisis real (relleno viejo o pendiente)."""
    n = _norm(senal)
    return ("prospecto con actividad relevante" in n) or ("pendiente de analisis" in n)


def _normalizar_servicio(serv: str) -> str:
    import unicodedata
    s = (serv or "").strip()
    if not s:
        return "Por identificar"
    key = unicodedata.normalize("NFKD", s)
    key = "".join(c for c in key if not unicodedata.combining(c)).lower().strip()
    return SERVICIOS_CANONICOS.get(key, s.capitalize())


def _firma(tipo, evidencias):
    """Huella de la evidencia de un prospecto: si no cambia, el análisis anterior sigue vigente."""
    import hashlib
    base = json.dumps([tipo] + [[e.get("titulo", ""), e.get("fecha", "")] for e in evidencias],
                      ensure_ascii=False)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _dias_desde(fecha_txt):
    from datetime import datetime, timezone
    try:
        f = datetime.strptime(str(fecha_txt)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - f).days
    except (TypeError, ValueError):
        return None


def _desde_previo(previo):
    """Análisis guardado en la corrida anterior, en el formato que usa main()."""
    data = {"servicio": previo.get("servicio", ""), "nivel": previo.get("nivel_encaje", "Medio"),
            "senal": previo.get("senal", ""), "recomendacion": previo.get("recomendacion", ""),
            "por_que": previo.get("por_que", "")}
    evid = {"titulo": previo.get("evidencia", ""), "fecha": previo.get("evidencia_fecha", ""),
            "url": previo.get("evidencia_url", "")}
    return data, evid, str(previo.get("fecha_senal", "") or ""), str(previo.get("analizado", "") or "")


def main():
    from datetime import datetime, timezone
    from azure.data.tables import TableServiceClient, UpdateMode
    from groq import Groq
    escribir = "--escribir" in sys.argv
    tope = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cliente = Groq(api_key=_groq_key())
    tsc = TableServiceClient.from_connection_string(_conn())
    tv = tsc.get_table_client(TABLA_VEREDICTOS)

    print("Cargando señales de Silver...")
    _cargar_silver()
    _cargar_competidores()

    prospectos = []
    excluidos = 0
    for e in tv.list_entities():
        if e.get("tipo_veredicto") not in TIPOS_VENTA:
            continue
        nombre = (e.get("entidad_nombre") or "").strip()
        if not nombre:
            continue
        nombre_limpio = re.sub(r"\s*\(cliente de.*?\)", "", nombre, flags=re.IGNORECASE).strip()
        if _es_competidor(nombre_limpio):
            excluidos += 1
            continue
        prospectos.append(e)
    if tope is not None:
        prospectos = prospectos[:tope]
    print(f"Prospectos de venta: {len(prospectos)}  (excluidos {excluidos} competidores)")

    # Análisis de la corrida anterior, por prospecto (sin textos de relleno).
    previos = {}
    try:
        for p in tsc.get_table_client(TABLA_VENTAS).list_entities():
            senal_p = str(p.get("senal", "") or "")
            if senal_p and not _es_relleno(senal_p):
                previos[_norm(p.get("prospecto", ""))] = p
    except Exception:
        pass

    # ── 1) Contexto de cada prospecto y decisión: ¿hace falta volver a analizarlo? ──
    # Groq gratis da 200.000 tokens en 24 h (ventana móvil) y el tablero también los usa.
    # Por eso solo se analizan los prospectos NUEVOS, los que tienen EVIDENCIA NUEVA o los
    # que llevan más de DIAS_VIGENCIA_ANALISIS días sin revisar; el resto conserva su
    # análisis. Y nunca más de MAX_ANALISIS_POR_CORRIDA por corrida.
    items = []
    for e in prospectos:
        nombre = e.get("entidad_nombre", "")
        tipo = e.get("tipo_veredicto", "")
        try:
            score = float(e.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        nombre_limpio = re.sub(r"\s*\(cliente de.*?\)", "", nombre, flags=re.IGNORECASE).strip()
        noticias = _noticias_de(nombre_limpio)
        secop_items = _secop_items_de(nombre_limpio)
        evidencias = _evidencias(noticias, secop_items)
        firma = _firma(tipo, evidencias)
        previo = previos.get(_norm(nombre_limpio))
        if previo is None:
            motivo, prioridad = "nuevo", 0
        elif str(previo.get("firma_evidencia", "") or "") != firma:
            motivo, prioridad = "evidencia nueva", 1
        else:
            dias = _dias_desde(previo.get("analizado"))
            if dias is None or dias >= DIAS_VIGENCIA_ANALISIS:
                motivo, prioridad = f"revisión periódica ({dias} días)", 2
            else:
                motivo, prioridad = "vigente", 9
        items.append({"e": e, "nombre": nombre, "nombre_limpio": nombre_limpio, "tipo": tipo,
                      "score": score, "noticias": noticias, "secop": secop_items,
                      "evidencias": evidencias, "firma": firma, "previo": previo,
                      "motivo": motivo, "prioridad": prioridad})

    candidatos = sorted((x for x in items if x["prioridad"] < 9),
                        key=lambda x: (x["prioridad"], str((x["previo"] or {}).get("analizado", "")),
                                       -x["score"]))
    a_analizar = {id(x) for x in candidatos[:MAX_ANALISIS_POR_CORRIDA]}
    print(f"Por analizar con IA: {len(a_analizar)} (de {len(candidatos)} que lo necesitan; "
          f"tope {MAX_ANALISIS_POR_CORRIDA}). Vigentes sin cambios: {len(items) - len(candidatos)}.")

    # ── 2) Análisis y armado de filas ──
    conservados = pendientes = analizados = 0
    groq_agotado = False
    filas = []
    hechos = 0
    for x in items:
        tipo, score = x["tipo"], x["score"]
        nombre_limpio, previo, evidencias = x["nombre_limpio"], x["previo"], x["evidencias"]
        competidor = _competidor_de(x["nombre"])
        n_secop = len(x["secop"])
        origen = "noticia" if x["noticias"] else ("secop" if n_secop > 0 else "general")
        fecha_senal = x["noticias"][0].get("fecha", "") if x["noticias"] else ""
        evid_prev = None
        analizado = hoy
        data = None

        if id(x) in a_analizar and not groq_agotado:
            try:
                resp = cliente.chat.completions.create(
                    model=MODELO_GROQ,
                    messages=[{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": _prompt(nombre_limpio, tipo, f"{score:.2f}", evidencias, n_secop)}],
                    temperature=0.5, max_tokens=220,
                )
                txt = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
                data = json.loads(txt)
                analizados += 1
                time.sleep(PAUSA_SEG)
            except Exception as ex:
                print(f"  ERROR {nombre_limpio}: {ex}")
                if "429" in str(ex) or "rate_limit" in str(ex).lower():
                    # Sin cupo: no se vuelve a llamar a Groq en esta corrida.
                    groq_agotado = True
                    print("  Groq sin cupo: el resto de prospectos conserva su análisis anterior.")

        if data is None:
            if previo:
                # Análisis vigente (o sin cupo de IA): se conserva el anterior, con su fecha.
                data, evid_prev, fecha_senal, analizado = _desde_previo(previo)
                conservados += 1
            else:
                # Prospecto nuevo sin análisis: pendiente (el tablero no lo muestra).
                data = {"servicio": "Por identificar", "nivel": "Pendiente",
                        "senal": TEXTO_PENDIENTE, "recomendacion": "Se completa en la próxima corrida.",
                        "por_que": ""}
                analizado = ""
                pendientes += 1

        # EVIDENCIA: la noticia o el proceso SECOP en que se basó la IA.
        evid = {"titulo": "", "fecha": "", "url": ""}
        if evid_prev is not None:
            evid = evid_prev
        else:
            try:
                n_ev = int(data.get("evidencia", 0) or 0)
            except (TypeError, ValueError):
                n_ev = 0
            if 1 <= n_ev <= len(evidencias):
                evid = dict(evidencias[n_ev - 1])
            elif evidencias and data.get("senal") != TEXTO_PENDIENTE:
                evid = dict(evidencias[0])
            if evid.get("origen") == "secop":
                evid["titulo"] = "SECOP: " + evid["titulo"][:150]
        if evid.get("fecha"):
            fecha_senal = evid["fecha"]   # la fecha de la señal = la de su evidencia
        fila = {
            "PartitionKey": ("lead" if tipo == "lead_score" else "captura"),
            "RowKey": re.sub(r"[^a-zA-Z0-9]", "_", nombre_limpio)[:80] + f"_{hechos}",
            "prospecto": nombre_limpio,
            "tipo": ("Lead" if tipo == "lead_score" else "Captura"),
            "competidor": competidor,
            "score": score,
            "servicio": _normalizar_servicio(str(data.get("servicio", ""))),
            "nivel_encaje": str(data.get("nivel", "Medio")),
            "senal": str(data.get("senal", "")),
            "recomendacion": str(data.get("recomendacion", "")),
            "origen": origen,
            "n_secop": n_secop,
            "fecha_senal": fecha_senal,
            "por_que": str(data.get("por_que", "") or ""),
            "evidencia": str(evid.get("titulo", "") or ""),
            "evidencia_fecha": str(evid.get("fecha", "") or ""),
            "evidencia_url": str(evid.get("url", "") or ""),
            # Para la próxima corrida: con qué evidencia y cuándo se hizo este análisis.
            # Si el análisis se conservó sin cupo de IA, la firma vieja obliga a re-analizarlo.
            "firma_evidencia": (x["firma"] if (data is not None and evid_prev is None and analizado == hoy)
                                else str((previo or {}).get("firma_evidencia", "") or "")),
            "analizado": analizado,
        }
        filas.append(fila)
        hechos += 1
        estado = "IA" if (evid_prev is None and analizado == hoy) else ("conservado" if previo else "pendiente")
        print(f"  [{hechos}] {nombre_limpio[:20]:20} [{fila['tipo']}] [{estado}: {x['motivo']}] SEÑAL: {fila['senal']}")
        print(f"       {fila['servicio']} porque: {fila['por_que'][:110]}  | fuente: {fila['evidencia'][:60]}")

    # Resumen: cuántos tienen una necesidad de TI concreta (los que muestra el tablero).
    # Los "Por identificar" se guardan igual (trazabilidad), pero el tablero los oculta.
    for etiqueta in ("Lead", "Captura"):
        grupo = [f for f in filas if f["tipo"] == etiqueta]
        accion = [f for f in grupo if f["servicio"] != "Por identificar"
                  and not _es_relleno(f["senal"])]
        print(f"  {etiqueta}s: {len(accion)} con servicio TAK identificado "
              f"(se muestran) de {len(grupo)}; {len(grupo) - len(accion)} sin necesidad TI (ocultos).")
    print(f"  Análisis con IA hoy: {analizados}. Conservados del análisis anterior: {conservados}. "
          f"Pendientes: {pendientes}.")

    if not escribir:
        print("\n[SIMULACRO] No escribí la tabla. Corre con --escribir para crearla.")
        return

    try:
        tsc.delete_table(TABLA_VENTAS)
        print("  (tabla vieja borrada, esperando a que Azure la libere...)")
    except Exception:
        pass
    tabla = None
    for _i in range(12):
        try:
            tabla = tsc.create_table_if_not_exists(TABLA_VENTAS)
            break
        except Exception as ex:
            if "BeingDeleted" in str(ex) or "Conflict" in str(ex):
                print(f"     Azure libera la tabla; espero 15s ({_i+1}/12)...")
                time.sleep(15)
            else:
                raise
    if tabla is None:
        print("La tabla sigue en borrado. Espera 1 min y reintenta."); return
    for f in filas:
        tabla.upsert_entity(f, mode=UpdateMode.REPLACE)
    print(f"\nListo. Tabla '{TABLA_VENTAS}' con {len(filas)} prospectos. Refresca Power BI.")
    if groq_agotado:
        print("  Groq se quedó sin cupo en esta corrida: Rate limit. Lo pendiente se analiza mañana.")


if __name__ == "__main__":
    main()