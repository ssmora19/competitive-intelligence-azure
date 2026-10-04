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
                    _cache_not.setdefault(ent, []).append({"titulo": tit, "fecha": fecha})
            elif fuente in FUENTES_SECOP:
                _cache_secop[ent] = _cache_secop.get(ent, 0) + 1


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


def _secop_de(nombre):
    _cargar_silver()
    obj = _norm(nombre)
    total = 0
    for ent, n in _cache_secop.items():
        if obj and (obj in ent or ent in obj):
            total += n
    return total


def _competidor_de(nombre_completo):
    m = re.search(r"cliente de\s+(.+?)\)", nombre_completo or "", flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


SYSTEM = (
    "Eres asesor comercial de TAK, empresa de TI colombiana. Portafolio: nube, "
    "inteligencia artificial, machine learning, ciberseguridad, bases de datos Oracle, "
    "business intelligence, infraestructura y blockchain. Para el prospecto que te doy, "
    "responde SOLO un JSON válido con TRES campos separados: "
    '{"servicio":"<servicio del portafolio que mejor encaje>",'
    '"nivel":"<Alto o Medio>",'
    '"senal":"<el HECHO real detectado del prospecto, máx 14 palabras, sin recomendar nada, '
    'solo qué está pasando. Ej: Ampliaron su red de infraestructura>",'
    '"recomendacion":"<la ACCIÓN que TAK debe tomar, máx 16 palabras, qué ofrecer y por qué. '
    'Ej: Ofrécele nube escalable para soportar ese crecimiento>"}. '
    "La SEÑAL sale de la noticia más relevante que te doy (el hecho puro). Si no hay noticia "
    "pero es muy activo en contratación pública (SECOP), la señal es eso (ej: Muy activo en "
    "licitaciones públicas). La RECOMENDACIÓN conecta ese hecho con un servicio de TAK. "
    "Varía los verbos. No inventes hechos falsos. IMPORTANTE: si la noticia NO se relaciona "
    "con una necesidad tecnológica real (ej: es sobre un hecho militar, una tragedia, política "
    "o deportes), NO fuerces una recomendación de TI rebuscada. En ese caso pon en senal una "
    "descripción NEUTRA de la entidad y una recomendación general de acercamiento comercial, "
    "sin inventar conexiones forzadas con ciberseguridad o nube. "
    "REGLA CLAVE: solo recomienda un servicio ESPECÍFICO (nube, ciberseguridad, etc.) cuando "
    "haya una señal clara en la noticia que lo justifique. Si solo sabes que la entidad es "
    "activa en SECOP pero no QUÉ necesita, la recomendación debe ser investigar/explorar sus "
    "licitaciones, NO ofrecer un servicio que no sabes si necesita. Nunca inventes la necesidad."
)


def _prompt(nombre, tipo, score, noticias, n_secop):
    tipoTxt = "cliente propio interesado" if tipo == "lead_score" else "cliente de la competencia capturable"
    if noticias:
        contexto = "Noticias recientes:\n" + "\n".join(f"  - {t['titulo']}" for t in noticias)
    elif n_secop > 0:
        contexto = (f"Sin noticias recientes, pero es MUY ACTIVO en contratación pública: "
                    f"{n_secop} procesos/contratos en SECOP. OJO: no sabemos QUÉ tecnología "
                    f"necesita (no hay noticia que lo indique), solo que contrata mucho. Por eso "
                    f"la recomendación NO debe inventar un servicio específico; debe sugerir "
                    f"INVESTIGAR sus licitaciones para detectar qué compra. El servicio ponlo como "
                    f"'Por identificar'.")
    else:
        contexto = "Sin noticias ni actividad SECOP relevante."
    return (
        f"Prospecto: {nombre}\nTipo: {tipoTxt}\nPrioridad (0-1): {score}\n{contexto}\n\n"
        f"Genera el JSON con servicio, nivel, senal y recomendacion."
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


def _normalizar_servicio(serv: str) -> str:
    import unicodedata
    s = (serv or "").strip()
    if not s:
        return "Por identificar"
    key = unicodedata.normalize("NFKD", s)
    key = "".join(c for c in key if not unicodedata.combining(c)).lower().strip()
    return SERVICIOS_CANONICOS.get(key, s.capitalize())


def main():
    from azure.data.tables import TableServiceClient, UpdateMode
    from groq import Groq
    escribir = "--escribir" in sys.argv
    tope = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)

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

    print(f"Prospectos de venta: {len(prospectos)}  (excluidos {excluidos} competidores)")
    filas = []
    hechos = 0
    for e in prospectos:
        if tope is not None and hechos >= tope:
            break
        nombre = e.get("entidad_nombre", "")
        tipo = e.get("tipo_veredicto", "")
        try:
            score = float(e.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        nombre_limpio = re.sub(r"\s*\(cliente de.*?\)", "", nombre, flags=re.IGNORECASE).strip()
        competidor = _competidor_de(nombre)
        noticias = _noticias_de(nombre_limpio)
        n_secop = _secop_de(nombre_limpio)
        origen = "noticia" if noticias else ("secop" if n_secop > 0 else "general")
        # NOVEDAD: fecha de la señal = fecha de la noticia más reciente
        fecha_senal = noticias[0].get("fecha", "") if noticias else ""
        try:
            resp = cliente.chat.completions.create(
                model=MODELO_GROQ,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": _prompt(nombre_limpio, tipo, f"{score:.2f}", noticias, n_secop)}],
                temperature=0.5, max_tokens=120,
            )
            txt = resp.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
            data = json.loads(txt)
        except Exception as ex:
            print(f"  ERROR {nombre_limpio}: {ex}")
            data = {"servicio": "Infraestructura TI", "nivel": "Medio", "senal": "Prospecto con actividad relevante.", "recomendacion": "Contactar para explorar necesidades."}
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
            "fecha_senal": fecha_senal,   # NOVEDAD
        }
        filas.append(fila)
        hechos += 1
        print(f"  [{hechos}] {nombre_limpio[:20]:20} [{fila['tipo']}] [{fecha_senal or 'sin fecha'}] SEÑAL: {fila['senal']}")
        time.sleep(PAUSA_SEG)

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
    print(f"\nListo. Tabla '{TABLA_VENTAS}' con {len(filas)} prospectos (con fecha_senal). Refresca Power BI.")


if __name__ == "__main__":
    main()