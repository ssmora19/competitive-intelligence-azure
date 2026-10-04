# -*- coding: utf-8 -*-
"""
config_eventos.py
=================

QUÉ HACE
--------
Centraliza la configuración de la clasificación de eventos por modelo: las
etiquetas de evento posibles, el modelo de Hugging Face a usar, la plantilla de
hipótesis para el clasificador zero-shot, y los umbrales de decisión.

PARA QUÉ SIRVE
--------------
Es el único lugar donde se ajusta este componente. Los tres scripts del módulo
(prueba_minima.py, validacion.py, clasificar_eventos.py) leen de aquí, así que
cambiar el modelo, agregar una etiqueta o mover un umbral se hace en un solo sitio.
Mismo principio que config_gold.py: configuración separada del código.

CONTEXTO EN LA ARQUITECTURA
---------------------------
Este submódulo vive DENTRO de Silver (ingestion_silver/clasificacion_eventos/).
Es el enriquecimiento por MODELO, complementario al enriquecimiento por reglas
de enriquecimiento.py. Clasifica el evento de negocio real de una noticia
(alianza, lanzamiento…) que las reglas dejaban en el genérico 'noticia'.

Funciona igual en dos regímenes:
  - Backfill (hoy): clasifica el histórico acumulado en una pasada batch.
  - Operación continua (futuro): clasifica cada noticia nueva del delta.
En ambos casos hace lo mismo — determinar el evento — por eso el nombre es
"clasificación de eventos", no "reclasificación".
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# MOTOR DE CLASIFICACIÓN
# ---------------------------------------------------------------------------
# 'groq'  → inferencia en la nube (rápido, preciso, gratis). Recomendado.
# 'local' → modelo de Hugging Face en tu PC (sin internet, pero lento). Respaldo.
MOTOR = "groq"

# --- Opción LOCAL (Hugging Face, corre en tu PC) ---
# mDeBERTa multilingüe zero-shot. Se dejó como respaldo; en pruebas resultó lento
# en hardware modesto (~97 s/noticia). Solo se usa si MOTOR = "local".
MODELO_LOCAL = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"

# --- Opción GROQ (inferencia en la nube) ---
# Llama 3.3 70B: modelo capaz de captar el matiz (distingue una alianza real de un
# "se une" de una conmemoración). Corre en el hardware LPU de Groq, no en tu PC.
# Si Groq quita este modelo del tier gratuito, cámbialo aquí por otro de su lista.
MODELO_GROQ = "qwen/qwen3.8-27b"

# Nombre de la variable (en local.settings.json o entorno) con la API key de Groq.
GROQ_KEY_ENV = "GROQ_API_KEY"

# Pausa entre llamadas para respetar el límite del tier gratuito (30 peticiones/min
# → mínimo ~2 s entre llamadas). Evita el error 429 (too many requests).
GROQ_PAUSA_SEG = 2.1

# ---------------------------------------------------------------------------
# ETIQUETAS DE EVENTO
# ---------------------------------------------------------------------------
# A qué eventos de negocio puede pertenecer una noticia. Son las señales de
# EVENTO de tu taxonomía real (las que valen la pena rescatar del genérico
# 'noticia'). 'otro_sin_evento' es la válvula de escape: para noticias que no son
# ningún evento de negocio, para no forzar una clasificación falsa.
ETIQUETAS = [
    "alianza",
    "lanzamiento",
    "expansion",
    "inversion",
    "contratacion_publica",
    "regulatorio",
    "actividad_tecnica",
    "otro_sin_evento",
]

# Descripción legible de cada etiqueta (útil para reportes y para la defensa).
DESCRIPCION_ETIQUETAS = {
    "alianza": "acuerdo, asociación o partnership entre empresas",
    "lanzamiento": "lanzamiento de un producto, servicio o solución",
    "expansion": "expansión a nuevos mercados, regiones o sectores",
    "inversion": "inversión, financiación o movimiento de capital",
    "contratacion_publica": "licitación, contrato público o proceso de SECOP",
    "regulatorio": "regulación, norma, política pública o cambio legal",
    "actividad_tecnica": "actividad técnica, desarrollo o adopción tecnológica",
    "otro_sin_evento": "no corresponde a ningún evento de negocio específico",
}

# ---------------------------------------------------------------------------
# PLANTILLA DE HIPÓTESIS (para el clasificador zero-shot / NLI)
# ---------------------------------------------------------------------------
# El modelo NLI evalúa, para cada etiqueta, qué tan probable es la frase
# "Este texto trata sobre {etiqueta}". En español mejora la precisión frente a
# la plantilla por defecto en inglés.
HIPOTESIS = "Este texto trata sobre {}."

# ---------------------------------------------------------------------------
# UMBRALES DE DECISIÓN
# ---------------------------------------------------------------------------
# Si la confianza de la mejor etiqueta no supera este umbral, se considera que el
# modelo no está seguro y se deja como 'otro_sin_evento' (no se fuerza un evento).
UMBRAL_CONFIANZA = 0.50

# Solo se reclasifican noticias que ya pasan el umbral de relevancia de Silver.
# Así se concentra el cómputo en lo que de verdad importa (no en el ruido).
SOLO_RELEVANTES = True

# tipo_senal de origen que este módulo reclasifica. Las reglas dejaron muchos
# eventos escondidos bajo el genérico 'noticia'; ese es el objetivo.
TIPO_ORIGEN = "noticia"

# ---------------------------------------------------------------------------
# LÍMITE DE TEXTO
# ---------------------------------------------------------------------------
# El modelo no necesita el texto completo para clasificar el evento; recortar
# acelera y evita exceder la ventana del modelo.
MAX_CHARS_TEXTO = 1000