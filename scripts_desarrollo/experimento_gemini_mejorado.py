# -*- coding: utf-8 -*-
"""
experimento_gemini_mejorado.py
==============================
QUE HACE: mide los TRES aspectos (tema, sentimiento, relevancia) con prompts
OPTIMIZADOS en una sola pasada sobre los 80 registros de validacion. Cada aspecto
usa tecnicas de prompt engineering para maximizar la precision:
  - RELEVANCIA: se le da la lista de las 91 entidades monitoreadas + criterio
    protagonista/evento (conocimiento de dominio que antes no tenia).
  - TEMA: categorias bien definidas + ejemplos resueltos (few-shot) + reglas de
    desempate para categorias que se solapan.
  - SENTIMIENTO: definicion explicita de positivo/neutro/negativo (como TONO) +
    ejemplos, para alinear el criterio de Gemini con el de la validacion.

PARA QUE SIRVE: aprovechar al maximo los 80 registros (una sola corrida) para ver
si los tres umbrales mejoran respecto a la linea base:
  - Tema:        Gemini generico 71.2%  | Reglas 52.5%
  - Sentimiento: Gemini generico 41.2%  | Reglas 82.5%
  - Relevancia:  Gemini generico 71.2%  | Reglas 81.2%

USO:
    python experimento_gemini_mejorado.py
"""
import os, sys, csv, json, time
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from ingestion_silver import entidades as ent_mod

MODELOS = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash"]
ARCHIVO = "conjunto_validacion_final.csv"
SALIDA = "resultados_gemini_mejorado.csv"
PAUSA = 5
MAX_REINTENTOS = 4


def get_key():
    k = os.environ.get("GEMINI_API_KEY", "")
    if k: return k
    with open("local.settings.json", encoding="utf-8") as f:
        return json.load(f).get("Values", {}).get("GEMINI_API_KEY", "")


def elegir_modelo(client):
    for m in MODELOS:
        try:
            client.models.generate_content(model=m, contents="ok",
                config=types.GenerateContentConfig(max_output_tokens=5))
            print(f"Modelo activo: {m}")
            return m
        except Exception as e:
            print(f"  {m} no disponible ({str(e)[:45]}...)")
    sys.exit(1)


# Lista de entidades para el bloque de relevancia.
ENTIDADES = sorted(ent_mod.cargar_entidades("."))
LISTA_ENTIDADES = "\n".join(f"- {e}" for e in ENTIDADES)

# ---------------------------------------------------------------------------
# PROMPT UNICO que pide los tres aspectos con criterios optimizados.
# ---------------------------------------------------------------------------
PROMPT = """Eres un analista de inteligencia competitiva para TAK, una empresa de TI en Colombia.
Analiza el contenido (noticia o publicacion en redes) y devuelve un JSON con tres claves: "tema", "sentimiento", "relevante". Responde SOLO el JSON, sin texto adicional.

============================================================
1) TEMA  -> uno de estos valores exactos:
- "lanzamiento": presentan un producto/servicio/funcionalidad NUEVA. Ej: "Introducing Pixel Tag", "We're launching the AI Economy index".
- "alianza": dos o mas organizaciones se asocian/colaboran. Ej: "Google Cloud announced a partnership with X", "IBM se alia con OpenAI".
- "inversion": adquisiciones, compras de empresas, gestion de activos, rondas de capital. Ej: "Bancolombia Capital: US$700M en activos", "Ecopetrol avanza en la compra de Brava".
- "expansion": abren oficina/planta/mercado nuevo, crecimiento geografico. Ej: "Sail Tower, our newest Austin office", "expansion hacia nuevos mercados".
- "regulatorio": normas, leyes, certificaciones (ISO), politicas gubernamentales. Ej: "obtiene la Trinorma ISO", "nuevo policy paper".
- "demanda_talento": contratacion, vacantes, perfiles, procesos de seleccion. Ej: "great technical interview", "Meet Wei, a Forward Deployed Engineer".
- "contratacion_publica": procesos/contratos con el Estado (SECOP, licitaciones).
- "contenido_corporativo": comunicacion institucional sin evento concreto (cultura, valores, testimonios, opinion de marca). Ej: "This is Bmind", "True leadership is about...".
- "contenido_audiovisual": el contenido ES un video/reel/podcast cuyo eje es el formato audiovisual mismo.
- "actividad_tecnica": tutoriales, cursos, explicaciones tecnicas, repos. Ej: "Get an introduction to AI fundamentals", "What are AI Loops?".
- "noticia": hecho noticioso de un tercero que no encaja en las anteriores.
- "referencia_web": pagina indice/producto/tramite/ficha, no una noticia. Ej: ficha de producto, pagina de inscripcion a un tramite.
REGLA DE DESEMPATE: si dudas entre "contenido_corporativo" y un evento concreto (lanzamiento/alianza/inversion/expansion/regulatorio/demanda_talento), elige el EVENTO concreto. Si es claramente un video como formato central, "contenido_audiovisual" gana a "contenido_corporativo".

============================================================
2) SENTIMIENTO  -> evalua el TONO del texto (no el impacto de negocio). Uno de:
- "positivo": celebra un logro, premio, exito, lanzamiento entusiasta, buen resultado. Ej: "ganamos premio a mejor empresa", "orgullosos de lanzar".
- "negativo": informa un problema, crisis, perdida, demanda, sancion, despido, tragedia. Ej: "enfrenta demanda", "pobreza aumenta", "atentado".
- "neutro": informa sin carga emocional clara; incluye la MAYORIA de comunicados corporativos normales, anuncios factuales, y noticias informativas. Ej: "publicamos el reporte trimestral", "la empresa anuncia cambios en su junta".
REGLA: ante la duda entre neutro y positivo, elige "neutro" salvo que haya una celebracion o logro EXPLICITO.

============================================================
3) RELEVANTE  -> "si" o "no", para inteligencia competitiva de TAK.
TAK monitorea EXCLUSIVAMENTE a estas entidades (clientes, competidores y clientes de competidores):
{entidades}

Es "si" (relevante) solo si se cumplen AMBAS:
  a) Menciona a una entidad de la lista como PROTAGONISTA (no de pasada, no como simple fuente citada del dato), O trata claramente del sector TI/tecnologia en Colombia.
  b) Describe un evento de negocio: contrato, alianza, lanzamiento, inversion/adquisicion, expansion, cambio regulatorio, o movimiento de talento.
Es "no" (ruido) si: no menciona ninguna entidad de la lista, o solo la menciona de pasada, o es farandula/deportes/sucesos policiales/resultados financieros generales sin evento, o la entidad es solo la fuente del dato (ej: "segun el DANE...").

============================================================
Contenido a evaluar:
Titulo: {titulo}
Descripcion: {descripcion}

Responde SOLO el JSON: {{"tema": "...", "sentimiento": "...", "relevante": "si|no"}}"""


def clasificar(client, modelo, titulo, desc):
    p = PROMPT.format(entidades=LISTA_ENTIDADES, titulo=(titulo or "")[:300], descripcion=(desc or "")[:800])
    for intento in range(MAX_REINTENTOS):
        try:
            r = client.models.generate_content(model=modelo, contents=p,
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"))
            data = json.loads(r.text)
            return (str(data.get("tema", "")).strip().lower(),
                    str(data.get("sentimiento", "")).strip().lower(),
                    str(data.get("relevante", "")).strip().lower())
        except genai_errors.APIError as e:
            code = getattr(e, "code", None)
            if code in (429, 503) and intento < MAX_REINTENTOS - 1:
                print(f"      ({code}; reintento en {15*(intento+1)}s)")
                time.sleep(15 * (intento + 1)); continue
            if code == 429:
                return ("__CUOTA__", "", "")
            return (None, None, None)
        except Exception as e:
            print(f"      (error: {str(e)[:45]})")
            return (None, None, None)
    return (None, None, None)


def main():
    key = get_key()
    if not key:
        print("Falta GEMINI_API_KEY"); sys.exit(1)
    print(f"Entidades cargadas para el prompt de relevancia: {len(ENTIDADES)}")
    client = genai.Client(api_key=key)
    modelo = elegir_modelo(client)

    with open(ARCHIVO, encoding="utf-8-sig") as f:
        filas = [r for r in csv.DictReader(f) if r.get("tema_real", "").strip()]

    # Reanudar
    ya = {}
    if os.path.exists(SALIDA):
        with open(SALIDA, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("g_tema"):
                    ya[r["id"]] = r

    resultados = []
    for i, r in enumerate(filas, 1):
        rid = r["id"]
        if rid in ya:
            fila = ya[rid]
        else:
            gt, gs, gr = clasificar(client, modelo, r["titulo"], r["descripcion"])
            if gt == "__CUOTA__":
                print(f"\n*** CUOTA AGOTADA en registro {i}. Progreso guardado; reanuda manana. ***")
                break
            fila = {"id": rid, "titulo": r["titulo"][:50],
                    "tema_real": r["tema_real"], "sent_real": r["sent_real"], "rel_real": r["relevante_real"],
                    "tema_reglas": r["tema_predicho"], "sent_reglas": r["sent_predicho"],
                    "g_tema": gt or "", "g_sent": gs or "", "g_rel": gr or ""}
        resultados.append(fila)
        with open(SALIDA, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(fila.keys())); w.writeheader(); w.writerows(resultados)
        if rid not in ya:
            print(f"[{i}/{len(filas)}] {r['titulo'][:38]:38} T={str(fila['g_tema'])[:10]:10} S={str(fila['g_sent'])[:8]:8} R={fila['g_rel']}")
            time.sleep(PAUSA)

    # --- Reporte comparativo ---
    total = sum(1 for r in resultados if r["tema_real"].strip())
    def acc(pred, real):
        ok = sum(1 for r in resultados if r[real].strip() and r[pred].strip().lower() == r[real].strip().lower())
        return 100*ok/total if total else 0

    print("\n" + "="*64)
    print(f"RESULTADOS CON PROMPTS MEJORADOS  (n={total})")
    print("="*64)
    print(f"{'Metrica':<14}{'Gemini MEJORADO':<17}{'Gemini base':<14}{'Reglas':<10}{'Umbral'}")
    print("-"*64)
    print(f"{'Tema':<14}{acc('g_tema','tema_real'):<17.1f}{'71.2':<14}{'52.5':<10}85%")
    print(f"{'Sentimiento':<14}{acc('g_sent','sent_real'):<17.1f}{'41.2':<14}{'82.5':<10}80%")
    print(f"{'Relevancia':<14}{acc('g_rel','rel_real'):<17.1f}{'71.2':<14}{'81.2':<10}-")
    print("\n(Los valores 'base' y 'Reglas' son de experimentos previos, como referencia.)")


if __name__ == "__main__":
    main()