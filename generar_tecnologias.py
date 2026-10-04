# -*- coding: utf-8 -*-
"""
generar_tecnologias.py  (v4 — 3 estados: domina / ofrece / oportunidad)
======================================================================
Cruza TRES fuentes para dar tensión estratégica al panel de Mercado:
  1) DEMANDA del mercado    -> silver-tendencias (menciones por tecnología)
  2) Lo que TAK OFRECE       -> portafolio de su web (manual, abajo)
  3) Lo que TAK GANA de verdad -> goldContratos (contratos de TAK clasificados)

Estado resultante por tecnología:
  - "Domina"     : TAK la ofrece Y tiene contratos en ella.
  - "Sin explotar": TAK la ofrece pero NO tiene contratos (oportunidad interna).
  - "Oportunidad": el mercado la demanda pero TAK NO la ofrece (hueco de mercado).

USO:  python generar_tecnologias.py            # simulacro
      python generar_tecnologias.py --escribir  # escribe la tabla
"""
from __future__ import annotations
import os, sys, json, re

CONT_TENDENCIAS = "silver-tendencias"
TABLA_CONTRATOS = "goldContratos"
TABLA = "goldTecnologias"

# Patrones para clasificar CUALQUIER texto (tendencias o contratos) por tecnología.
CATEGORIAS = {
    "IA / Machine Learning": [r"\bmachine learning\b", r"\bdeep learning\b", r"\bllm\b", r"\bgpt\b",
        r"\binteligencia artificial\b", r"\bredes neuronales\b", r"\bchatbot\b", r"\bopenai\b", r"\banal[ií]tic"],
    "Cloud / Nube": [r"\baws\b", r"\bazure\b", r"\bgoogle cloud\b", r"\bcloud\b", r"\bnube\b",
        r"\bkubernetes\b", r"\bopenshift\b", r"\bserverless\b", r"\bpaas\b", r"\biaas\b", r"\bdrp\b", r"\bbackup\b"],
    "Ciberseguridad": [r"\bciberseguridad\b", r"\bcybersecurity\b", r"\bseguridad\b", r"\bfirewall\b",
        r"\bvulnerab", r"\bransomware\b", r"\bfortinet\b"],
    "Datos / BI": [r"\boracle\b", r"\bexadata\b", r"\bdata warehouse\b", r"\bdatabase\b", r"\bbase de datos\b",
        r"\bsnowflake\b", r"\bbusiness intelligence\b", r"\bpower bi\b", r"\bbig data\b", r"\blicenc"],
    "Infraestructura / DevOps": [r"\bterraform\b", r"\bdevops\b", r"\binfraestructura\b", r"\bdatacenter\b",
        r"\bdata center\b", r"\bservidor", r"\bdell\b", r"\bexpansi[óo]n de la red\b", r"\bhardware\b"],
    "Blockchain": [r"\bblockchain\b", r"\bweb3\b", r"\bcripto"],
}

# TAK OFRECE (según su web takcolombia.com.co). True = está en el portafolio.
TAK_OFRECE = {
    "IA / Machine Learning": True,
    "Cloud / Nube": True,
    "Ciberseguridad": True,
    "Datos / BI": True,
    "Infraestructura / DevOps": True,
    "Blockchain": True,
}


def _conn() -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env:
        return env
    for r in ("local.settings.json", os.path.join("..", "local.settings.json"),
              os.path.join("..", "..", "local.settings.json")):
        if os.path.exists(r):
            with open(r, encoding="utf-8") as f:
                v = json.load(f).get("Values", {})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"):
                return v["AZURE_STORAGE_CONNECTION_STRING"]
    print("No encontré AZURE_STORAGE_CONNECTION_STRING."); sys.exit(1)


def _clasificar(texto: str) -> str | None:
    t = (texto or "").lower()
    for cat, patrones in CATEGORIAS.items():
        for p in patrones:
            if re.search(p, t):
                return cat
    return None


def main() -> None:
    from azure.storage.blob import BlobServiceClient
    from azure.data.tables import TableServiceClient, UpdateMode
    escribir = "--escribir" in sys.argv

    bs = BlobServiceClient.from_connection_string(_conn())
    tsc = TableServiceClient.from_connection_string(_conn())

    # 1) DEMANDA del mercado (tendencias)
    demanda = {cat: 0 for cat in CATEGORIAS}
    cli = bs.get_container_client(CONT_TENDENCIAS)
    for blob in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        base = " ".join([str(d.get("termino_busqueda") or ""), str(d.get("titulo") or ""), str(d.get("descripcion") or "")])
        cat = _clasificar(base)
        if cat:
            demanda[cat] += 1

    # 2) Contratos de TAK clasificados por tecnología (de goldContratos, es_tak = Sí)
    tak_contratos = {cat: 0 for cat in CATEGORIAS}
    tc = tsc.get_table_client(TABLA_CONTRATOS)
    for e in tc.list_entities():
        if e.get("es_tak") != "Sí":
            continue
        cat = _clasificar(e.get("objeto", ""))
        if cat:
            tak_contratos[cat] += 1

    maxN = max(demanda.values()) if any(demanda.values()) else 1
    print("=" * 68)
    print("TECNOLOGÍAS: demanda / contratos TAK / estado")
    print("=" * 68)
    filas = []
    for cat in CATEGORIAS:
        dem = demanda[cat]
        ncontr = tak_contratos[cat]
        ofrece = TAK_OFRECE.get(cat, False)
        # decidir estado
        if not ofrece:
            estado = "Oportunidad"
        elif ncontr > 0:
            estado = "Domina"
        else:
            estado = "Sin explotar"
        nivel = "Alta" if dem >= maxN*0.6 else ("Media" if dem >= maxN*0.3 else "Baja")
        print(f"  {cat:26} dem={dem:3}  contratosTAK={ncontr:2}  ofrece={ofrece}  -> {estado} (demanda {nivel})")
        filas.append({
            "PartitionKey": "tecnologia",
            "RowKey": cat.replace(" ", "_").replace("/", "-"),
            "categoria": cat,
            "demanda_n": dem,
            "demanda": nivel,
            "contratos_tak": ncontr,
            "tak_ofrece": "Sí" if ofrece else "No",
            "estado": estado,
        })

    if not escribir:
        print("\n[SIMULACRO] No escribí la tabla. Corre con --escribir para crearla.")
        return

    tabla = tsc.create_table_if_not_exists(TABLA)
    for f in filas:
        tabla.upsert_entity(f, mode=UpdateMode.REPLACE)
    print(f"\nListo. Tabla '{TABLA}' con {len(filas)} tecnologías. Refresca Power BI.")


if __name__ == "__main__":
    main()