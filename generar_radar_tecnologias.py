# -*- coding: utf-8 -*-
"""
generar_radar_tecnologias.py
============================
Radar de tecnologías del mercado: cruza TRES fuentes para responderle al gerente
"qué tecnologías se están vendiendo/pidiendo que TAK podría adoptar":

  1) TENDENCIAS GLOBALES  -> silver-tendencias (GitHub, RSS tech, fabricantes)
                             = qué está emergiendo en el mundo
  2) DEMANDA COLOMBIANA    -> goldContratos (SECOP)
                             = qué se contrata de verdad en Colombia (con montos)
  3) PORTAFOLIO TAK        -> lista de servicios de TAK
                             = qué ofrece / qué NO ofrece

Con IA (Groq) extrae las tecnologías mencionadas en las tendencias, las agrupa,
y las cruza con la demanda SECOP y el portafolio. Produce la tabla
'goldRadarTecnologias' con un veredicto por tecnología.

USO:  python generar_radar_tecnologias.py            # simulacro
      python generar_radar_tecnologias.py --escribir  # escribe la tabla
"""
from __future__ import annotations
import os, sys, json, time, re
from collections import Counter

TABLA_DESTINO = "goldRadarTecnologias"
MODELO_GROQ = "qwen/qwen3.8-27b"
CLAVE_GROQ = "GROQ_API_KEY"

CONT_TENDENCIAS = "silver-tendencias"
FUENTES_TEND = {"github_trending", "rss_tech_general", "rss_fabricantes",
                "documentacion_tecnica", "reportes_sectoriales"}

# Portafolio oficial de TAK (de takcolombia.com)
PORTAFOLIO_TAK = [
    "inteligencia artificial", "ciberseguridad", "blockchain", "bases de datos",
    "capa media", "desarrollo de apps", "business intelligence",
    "virtualizacion", "infraestructura", "machine learning",
    "licencias oracle", "hardware", "nube",
]

# Tecnologías/categorías candidatas a rastrear (las que el radar evalúa).
# Se cuenta cuántas veces aparecen en tendencias globales y en SECOP.
TECNOLOGIAS_RADAR = {
    "Kubernetes / Contenedores": ["kubernetes", "docker", "container", "k8s", "openshift"],
    "Serverless": ["serverless", "lambda", "faas", "functions"],
    "IA Generativa / LLMs": ["llm", "gpt", "generative", "genai", "ia generativa", "copilot", "librechat"],
    "Machine Learning": ["machine learning", "ml ", "tensorflow", "pytorch", "modelo"],
    "Kafka / Streaming": ["kafka", "streaming", "pulsar", "event"],
    "Ciberseguridad": ["security", "seguridad", "trivy", "vulnerab", "ciberseguridad", "firewall"],
    "Data / Analytics": ["data", "analytics", "warehouse", "snowflake", "databricks", "bigdata"],
    "Cloud / Nube": ["cloud", "nube", "aws", "azure", "gcp", "iaas", "paas"],
    "DevOps / CI-CD": ["devops", "ci/cd", "pipeline", "terraform", "ansible", "traefik"],
    "Blockchain": ["blockchain", "ethereum", "web3", "smart contract"],
    "Bases de datos": ["database", "base de datos", "postgres", "oracle", "sql", "mongodb"],
    "Observabilidad": ["observability", "monitoring", "prometheus", "grafana", "telemetry"],
    "Low-code / No-code": ["low-code", "no-code", "lowcode"],
    "Robótica / RPA": ["rpa", "robotic", "automation", "automatizacion"],
}


# Fabricante / partner con quién TAK podría aliarse para ofrecer cada tecnología
FABRICANTES = {
    "Kafka / Streaming": "Confluent, Apache",
    "Kubernetes / Contenedores": "Red Hat OpenShift, VMware Tanzu",
    "Serverless": "AWS Lambda, Azure Functions",
    "IA Generativa / LLMs": "OpenAI, Anthropic, Microsoft, Google",
    "Machine Learning": "Google, AWS SageMaker, Databricks",
    "Ciberseguridad": "Palo Alto, Fortinet, CrowdStrike",
    "Data / Analytics": "Snowflake, Databricks",
    "Cloud / Nube": "AWS, Azure, Google Cloud, Oracle",
    "DevOps / CI-CD": "GitLab, HashiCorp, Atlassian",
    "Blockchain": "Ethereum, Hyperledger",
    "Bases de datos": "Oracle, MongoDB, PostgreSQL",
    "Observabilidad": "Datadog, Grafana, Elastic",
    "Low-code / No-code": "OutSystems, Mendix, Power Apps",
    "Robótica / RPA": "UiPath, Automation Anywhere",
}


# Fabricantes de los que TAK YA es partner (no sugerir "aliarse", ya lo tiene)
PARTNERS_ACTUALES_TAK = [
    "oracle", "aws", "amazon web services", "fortinet", "microsoft", "azure",
    "ibm", "dell", "red hat", "redhat", "nexsys", "td synnex", "synnex",
]


def _clasificar_fabricantes(fabricantes_str):
    """Separa los fabricantes en: los que TAK ya tiene (partner) y los nuevos.
    Devuelve (partners_actuales, nuevos) como strings."""
    if not fabricantes_str:
        return "", ""
    lista = [f.strip() for f in fabricantes_str.split(",")]
    actuales, nuevos = [], []
    for fab in lista:
        fn = _norm(fab)
        if any(p in fn for p in PARTNERS_ACTUALES_TAK):
            actuales.append(fab)
        else:
            nuevos.append(fab)
    return ", ".join(actuales), ", ".join(nuevos)


def _norm(t):
    import unicodedata
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower()


def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"): return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No hay conexión"); sys.exit(1)


def _groq_key():
    env = os.environ.get(CLAVE_GROQ)
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get(CLAVE_GROQ): return v[CLAVE_GROQ]
    print("No hay GROQ_API_KEY"); sys.exit(1)


def _tak_ofrece(tec_norm):
    """¿TAK ofrece esta tecnología? Compara con el portafolio."""
    for serv in PORTAFOLIO_TAK:
        if serv in tec_norm or tec_norm in serv:
            return True
    # mapeos específicos
    mapa = {
        "ciberseguridad": ["ciberseguridad"], "cloud": ["nube"], "nube": ["nube"],
        "bases de datos": ["bases de datos"], "machine learning": ["machine learning"],
        "ia generativa": ["inteligencia artificial"], "blockchain": ["blockchain"],
        "data": ["business intelligence", "bases de datos"], "analytics": ["business intelligence"],
    }
    for clave, servs in mapa.items():
        if clave in tec_norm:
            return True
    return False


def contar_tendencias(meses_recientes=6):
    """Cuenta cuántas veces aparece cada tecnología en las tendencias globales,
    SOLO contando las recientes (últimos N meses) para reflejar el boom actual."""
    from azure.storage.blob import BlobServiceClient
    from datetime import datetime, timezone, timedelta
    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(CONT_TENDENCIAS)
    corte = (datetime.now(timezone.utc) - timedelta(days=meses_recientes*30)).strftime("%Y-%m-%d")
    conteo = Counter()
    fecha_reciente = {}
    evidencia = {}          # artículo/repo MÁS RECIENTE que respalda cada tecnología
    for b in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        if d.get("fuente_original") not in FUENTES_TEND:
            continue
        fev = str(d.get("fecha_evento_utc") or "")[:10]
        # solo tendencias recientes
        if fev and fev < corte:
            continue
        texto = _norm((d.get("titulo","") or "") + " " + (d.get("descripcion","") or ""))
        for tec, patrones in TECNOLOGIAS_RADAR.items():
            if any(p in texto for p in patrones):
                conteo[tec] += 1
                if fev and (tec not in fecha_reciente or fev > fecha_reciente[tec]):
                    fecha_reciente[tec] = fev
                if tec not in evidencia or (fev or "") > evidencia[tec]["fecha"]:
                    evidencia[tec] = {"titulo": (d.get("titulo", "") or "").strip(),
                                      "fecha": fev or "", "url": d.get("url", "") or ""}
    return conteo, fecha_reciente, evidencia


# Contenedores Silver con contratos SECOP (los 3 rubros = toda la demanda)
CONT_SECOP_SILVER = ["silver-competidores", "silver-clientes", "silver-clientes-competencia"]
FUENTES_SECOP_TODAS = {
    "secop_competidor",
    "secop_cliente_contratos", "secop_cliente_procesos", "secop_cliente_adjudicatario",
    "secop_competencia_contratos", "secop_competencia_procesos",
}


def contar_secop():
    """Por cada tecnología: cuenta contratos, suma monto, junta ENTIDADES que la
    compran y la fecha más reciente. Lee de los 3 contenedores Silver (toda la
    demanda: competidores + clientes + clientes-competencia)."""
    from azure.storage.blob import BlobServiceClient
    bs = BlobServiceClient.from_connection_string(_conn())
    conteo = Counter()
    monto = Counter()
    entidades = {}
    fecha_reciente = {}
    evidencia = {}          # contrato MÁS RECIENTE que respalda cada tecnología
    for cont in CONT_SECOP_SILVER:
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
            if d.get("fuente_original") not in FUENTES_SECOP_TODAS:
                continue
            secop = d.get("secop") or {}
            texto = _norm((secop.get("objeto","") or "") + " " + (d.get("titulo","") or ""))
            try:
                val = float(secop.get("valor") or 0)
            except (TypeError, ValueError):
                val = 0
            # la entidad compradora
            ent = secop.get("entidad_contratante") or secop.get("entidad") or ""
            ffirma = str(secop.get("fecha_firma") or "")[:10]
            for tec, patrones in TECNOLOGIAS_RADAR.items():
                if any(p in texto for p in patrones):
                    conteo[tec] += 1
                    monto[tec] += val
                    if ent:
                        entidades.setdefault(tec, Counter())[ent] += 1
                    if ffirma and (tec not in fecha_reciente or ffirma > fecha_reciente[tec]):
                        fecha_reciente[tec] = ffirma
                    if tec not in evidencia or (ffirma or "") > evidencia[tec]["fecha"]:
                        evidencia[tec] = {
                            "objeto": (secop.get("objeto", "") or d.get("titulo", "") or "").strip(),
                            "entidad": ent, "fecha": ffirma or "", "url": d.get("url", "") or ""}
    return conteo, monto, entidades, fecha_reciente, evidencia


def descubrir_tecnologias_ia(muestra_titulos):
    """Usa Groq para extraer tecnologías/herramientas específicas de los títulos
    que NO están en la lista fija. Descubre lo 'nuevo' que el radar no anticipó."""
    from groq import Groq
    cliente = Groq(api_key=_groq_key())
    # nombres ya conocidos (para que la IA los ignore)
    conocidas = ", ".join(TECNOLOGIAS_RADAR.keys())
    bloque_titulos = "\n".join(f"- {t}" for t in muestra_titulos[:60])
    system = (
        "Eres analista de tendencias tecnológicas. Te doy títulos de repos de GitHub, "
        "noticias tech y contratos públicos. Extrae SOLO tecnologías, herramientas o "
        "plataformas ESPECÍFICAS y CONCRETAS que aparezcan repetidas y que sean "
        "relevantes para una empresa de TI. IGNORA las que ya están en esta lista: "
        f"{conocidas}. "
        "Responde SOLO un JSON: {\"nuevas\": [{\"nombre\":\"...\", \"veces\":N, \"que_es\":\"breve\"}]}. "
        "Máximo 8 tecnologías nuevas, las más frecuentes. Si no hay ninguna nueva relevante, "
        "devuelve lista vacía. No inventes."
    )
    try:
        resp = cliente.chat.completions.create(
            model=MODELO_GROQ,
            messages=[{"role":"system","content":system},
                      {"role":"user","content":f"Títulos:\n{bloque_titulos}"}],
            temperature=0.3, max_tokens=500,
        )
        txt = resp.choices[0].message.content.strip().replace("```json","").replace("```","").strip()
        data = json.loads(txt)
        return data.get("nuevas", [])
    except Exception as ex:
        print(f"  (descubrimiento IA falló: {ex})")
        return []


def _juntar_titulos():
    """Junta títulos de tendencias recientes + objetos de contratos, para la IA."""
    from azure.storage.blob import BlobServiceClient
    from azure.data.tables import TableServiceClient
    titulos = []
    bs = BlobServiceClient.from_connection_string(_conn())
    try:
        cli = bs.get_container_client(CONT_TENDENCIAS)
        for b in list(cli.list_blobs())[:120]:
            try:
                d = json.loads(cli.get_blob_client(b.name).download_blob().readall())
            except Exception:
                continue
            if d.get("fuente_original") in FUENTES_TEND:
                t = (d.get("titulo","") or "").strip()
                if t: titulos.append({"titulo": t, "url": d.get("url", "") or "",
                                      "fecha": str(d.get("fecha_evento_utc") or "")[:10]})
    except Exception:
        pass
    return titulos


def main():
    escribir = "--escribir" in sys.argv
    print("Contando tecnologías en tendencias globales...")
    tend, tend_fecha, tend_evid = contar_tendencias(meses_recientes=6)
    print("Contando tecnologías en contratos SECOP (Colombia)...")
    secop_n, secop_monto, secop_entidades, secop_fecha, secop_evid = contar_secop()
    print("Descubriendo tecnologías NUEVAS con IA (fuera de la lista)...")
    titulos_info = _juntar_titulos()
    nuevas_ia = descubrir_tecnologias_ia([t["titulo"] for t in titulos_info])

    filas = []
    orden = 0
    print("\n" + "=" * 90)
    print("RADAR DE TECNOLOGÍAS DEL MERCADO")
    print("=" * 90)
    print(f"  {'tecnología':28} {'global':>7} {'Col.SECOP':>10} {'$ Colombia':>16} {'TAK':>5} {'veredicto'}")
    print("  " + "-" * 86)

    for tec in TECNOLOGIAS_RADAR:
        g = tend.get(tec, 0)
        s = secop_n.get(tec, 0)
        m = secop_monto.get(tec, 0)
        ofrece = _tak_ofrece(_norm(tec))
        # veredicto
        if s > 0 and not ofrece:
            veredicto = "OPORTUNIDAD (se pide en Colombia, TAK no ofrece)"
        elif g >= 5 and s == 0 and not ofrece:
            veredicto = "EMERGENTE (boom global, evaluar)"
        elif ofrece and s > 0:
            veredicto = "FORTALECER (TAK ya ofrece y hay demanda)"
        elif ofrece:
            veredicto = "EN PORTAFOLIO"
        elif g >= 3:
            veredicto = "VIGILAR (tendencia global)"
        else:
            veredicto = "Baja señal"
        # solo incluir las que tienen alguna señal
        if g == 0 and s == 0:
            continue
        orden += 1
        fabricante = FABRICANTES.get(tec, "")
        # top 3 entidades que compran esta tecnología
        ents = secop_entidades.get(tec, Counter())
        top_ents = " · ".join(n for n, _ in ents.most_common(3)) if ents else ""
        fecha_rec = secop_fecha.get(tec, "")
        print(f"  {tec[:26]:26} {g:>6} {s:>9} {m:>15,.0f} {('Sí' if ofrece else 'No'):>4}  {veredicto[:32]}")
        filas.append({
            "PartitionKey": "tecnologia",
            "RowKey": re.sub(r"[^a-zA-Z0-9]", "_", tec)[:60],
            "orden": orden,
            "tecnologia": tec,
            "senal_global": g,
            "contratos_colombia": s,
            "monto_colombia": float(m),
            "tak_ofrece": "Sí" if ofrece else "No",
            "veredicto": veredicto,
            "fabricante": fabricante,
            "partner_actual": _clasificar_fabricantes(fabricante)[0],
            "partner_nuevo": _clasificar_fabricantes(fabricante)[1],
            "entidades_compran": top_ents,
            "fecha_reciente": fecha_rec,
            "fecha_tendencia_global": tend_fecha.get(tec, ""),
            # SUSTENTO verificable: el contrato SECOP y el artículo más recientes
            "evid_contrato": secop_evid.get(tec, {}).get("objeto", "")[:200],
            "evid_contrato_entidad": secop_evid.get(tec, {}).get("entidad", ""),
            "evid_contrato_fecha": secop_evid.get(tec, {}).get("fecha", ""),
            "evid_contrato_url": secop_evid.get(tec, {}).get("url", ""),
            "evid_tendencia": tend_evid.get(tec, {}).get("titulo", "")[:200],
            "evid_tendencia_fecha": tend_evid.get(tec, {}).get("fecha", ""),
            "evid_tendencia_url": tend_evid.get(tec, {}).get("url", ""),
        })

    # agregar las tecnologías NUEVAS descubiertas por IA (fuera de la lista fija)
    if nuevas_ia:
        print("\n" + "=" * 90)
        print("TECNOLOGÍAS NUEVAS DESCUBIERTAS POR IA (no estaban en la lista):")
        print("=" * 90)
        for nv in nuevas_ia:
            nombre = str(nv.get("nombre",""))[:40]
            veces = nv.get("veces", 0)
            quees = str(nv.get("que_es",""))[:50]
            # sustento: el primer título que menciona la tecnología descubierta
            clave = _norm(nombre.split("(")[0]).strip()
            ev = next((t for t in titulos_info if clave and clave in _norm(t["titulo"])), {})
            print(f"  {nombre:40} (x{veces})  {quees}")
            orden += 1
            filas.append({
                "PartitionKey": "tecnologia",
                "RowKey": re.sub(r"[^a-zA-Z0-9]", "_", nombre)[:60] + f"_nueva_{orden}",
                "orden": orden,
                "tecnologia": nombre,
                "senal_global": veces if isinstance(veces,int) else 0,
                "contratos_colombia": 0,
                "monto_colombia": 0.0,
                "tak_ofrece": "No",
                "veredicto": "NUEVA - descubierta por IA (evaluar)",
                "fabricante": "",
                "entidades_compran": "",
                "fecha_reciente": "",
                "fecha_tendencia_global": "",
                "que_es": quees,
                "evid_tendencia": ev.get("titulo", "")[:200],
                "evid_tendencia_fecha": ev.get("fecha", ""),
                "evid_tendencia_url": ev.get("url", ""),
            })

    if not escribir:
        print("\n[SIMULACRO] No escribí la tabla. Corre con --escribir para crearla.")
        return

    from azure.data.tables import TableServiceClient, UpdateMode
    tsc = TableServiceClient.from_connection_string(_conn())
    try:
        tsc.delete_table(TABLA_DESTINO); time.sleep(3)
    except Exception:
        pass
    dest = None
    for _ in range(12):
        try:
            dest = tsc.create_table_if_not_exists(TABLA_DESTINO); break
        except Exception as ex:
            if "BeingDeleted" in str(ex) or "Conflict" in str(ex): time.sleep(15)
            else: raise
    for f in filas:
        dest.upsert_entity(f, mode=UpdateMode.REPLACE)
    print(f"\nListo. Tabla '{TABLA_DESTINO}' con {len(filas)} tecnologías. Refresca Power BI.")


if __name__ == "__main__":
    main()