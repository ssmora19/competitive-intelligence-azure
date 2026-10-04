# -*- coding: utf-8 -*-
"""
borrar_ruido_silver_clientes_competencia.py  (v2 — con alias)
=============================================================
Borra de silver-clientes-competencia SOLO las noticias RUIDOSAS, conservando
las buenas. v2: reconoce SIGLAS y NOMBRES CORTOS por entidad (alias), para no
botar noticias válidas como "Movistar" (Telefónica Movistar) o "JCC" (Junta
Central de Contadores).

Una noticia es VÁLIDA si el núcleo del nombre O cualquiera de sus alias aparece
en el texto.

SEGURIDAD: simulacro por defecto + ejemplos. Solo borra con --borrar.

USO:  python borrar_ruido_silver_clientes_competencia.py            # simulacro
      python borrar_ruido_silver_clientes_competencia.py --borrar    # borra
"""
from __future__ import annotations
import os, sys, json, re, unicodedata

CONTENEDOR = "silver-clientes-competencia"
FUENTE = "noticias_competencia"

# Alias / nombres alternativos por entidad (siglas, nombres cortos).
# Si CUALQUIERA aparece en el texto, la noticia se considera válida.
ALIAS = {
    "Junta Central de Contadores": ["jcc", "junta central contadores"],
    "Telefonica Movistar": ["movistar", "telefonica"],
    "Superintendencia Financiera de Colombia": ["superfinanciera", "superintendencia financiera"],
    "DITRA - Direccion de Transito y Transporte Policia Nacional": ["ditra", "transito y transporte", "direccion de transito"],
    "Direccion de Transito y Transporte Policia Nacional": ["ditra", "transito y transporte"],
    "DIAN": ["dian", "impuestos y aduanas"],
    "SENA": ["sena"],
    "ANSPE": ["anspe", "prosperidad social"],
    "IDIPRON": ["idipron"],
    "Gobernacion de Antioquia": ["gobernacion de antioquia", "antioquia"],
    "Ministerio de Justicia y del Derecho": ["minjusticia", "ministerio de justicia"],
    "Servicio Geologico Colombiano": ["servicio geologico", "sgc"],
    "EPS Famisanar": ["famisanar"],
    "La Previsora S.A.": ["previsora", "fiduprevisora"],
    "Grupo Bancolombia": ["bancolombia"],
    "Grupo Exito": ["exito"],
    "Sura": ["sura", "grupo sura"],
    "Nestle de Colombia": ["nestle"],
    "PepsiCo Colombia": ["pepsico", "pepsi"],
    "Tecnoquimicas": ["tecnoquimicas"],
    "Colombia Compra Eficiente": ["colombia compra eficiente", "cce"],
    "Banco Santander": ["santander"],
    "CaixaBank": ["caixabank", "caixa"],
    "Mapfre": ["mapfre"],
    "Mutua Madrilena": ["mutua madrilena", "mutua"],
    "El Corte Ingles": ["corte ingles"],
    "Toyota": ["toyota"],
    "LaLiga": ["laliga", "la liga"],
    "Endesa": ["endesa"],
    "Mediaset Espana": ["mediaset"],
    "Unidad Editorial": ["unidad editorial"],
    "Lowi": ["lowi"],
    "IDEMIA": ["idemia"],
    "Yupi": ["yupi"],
    "Davivienda": ["davivienda"],
}

_STOP = {"sa","sas","s","ltda","inc","corp","corporation","colombia","co","group",
         "grupo","de","del","la","el","los","las","y","the","consulting","consultores",
         "solutions","services","technology","tech","sistemas","latam","cliente",
         "eps","sac","spa","compania","seguros","banco"}


def _norm(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", t.lower())


def _limpiar_entidad(ent: str) -> str:
    return re.sub(r"\(cliente de.*?\)", "", ent or "", flags=re.IGNORECASE).strip()


def _nucleo(nombre: str) -> str:
    return " ".join(p for p in _norm(nombre).split() if p not in _STOP and len(p) > 2).strip()


def _es_valida(entidad: str, texto: str) -> bool:
    texto_norm = _norm(texto)
    palabras = set(texto_norm.split())
    # 1) por núcleo del nombre
    nucleo = _nucleo(entidad)
    if nucleo and (nucleo in texto_norm or all(p in palabras for p in nucleo.split())):
        return True
    # 2) por alias
    for alias in ALIAS.get(entidad, []):
        a = _norm(alias)
        if a and (a in texto_norm or all(p in palabras for p in a.split())):
            return True
    return False


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


def main() -> None:
    from azure.storage.blob import BlobServiceClient
    borrar = "--borrar" in sys.argv
    bs = BlobServiceClient.from_connection_string(_conn())
    cli = bs.get_container_client(CONTENEDOR)

    ruidosas, validas, otros = [], 0, 0
    ej_borrar, ej_conservar = [], []

    print(f"Revisando '{CONTENEDOR}'...\n")
    for blob in cli.list_blobs():
        try:
            d = json.loads(cli.get_blob_client(blob.name).download_blob().readall())
        except Exception:
            continue
        if d.get("fuente_original") != FUENTE:
            otros += 1
            continue
        entidad = _limpiar_entidad(d.get("entidad_monitoreada") or d.get("entidad_declarada") or "")
        texto = " ".join([str(d.get("titulo") or ""), str(d.get("descripcion") or "")])
        titulo = (d.get("titulo") or "")[:60]
        if not _nucleo(entidad):
            validas += 1
            continue
        if _es_valida(entidad, texto):
            validas += 1
            if len(ej_conservar) < 8:
                ej_conservar.append((entidad, titulo))
        else:
            ruidosas.append(blob.name)
            if len(ej_borrar) < 15:
                ej_borrar.append((entidad, titulo))

    print("=" * 56)
    print("RESUMEN")
    print("=" * 56)
    print(f"  Noticias RUIDOSAS a borrar: {len(ruidosas)}")
    print(f"  Noticias VÁLIDAS (conservar): {validas}")
    print(f"  Otros blobs (no noticias):    {otros}")

    print("\n--- EJEMPLOS QUE BORRARÍA (verifica que sea ruido) ---")
    for e, t in ej_borrar:
        print(f"  '{e}' -> {t}")
    print("\n--- EJEMPLOS QUE CONSERVA ---")
    for e, t in ej_conservar:
        print(f"  '{e}' -> {t}")

    if not borrar:
        print("\n[SIMULACRO] No borré nada. Revisa los ejemplos. Corre con --borrar si están bien.")
        return

    print(f"\nBorrando {len(ruidosas)} noticias ruidosas...")
    n = 0
    for nombre in ruidosas:
        try:
            cli.get_blob_client(nombre).delete_blob()
            n += 1
        except Exception as ex:
            print(f"  error: {nombre}: {ex}")
    print(f"Listo. Borré {n} ruidosas. Quedan {validas} noticias válidas.")


if __name__ == "__main__":
    main()