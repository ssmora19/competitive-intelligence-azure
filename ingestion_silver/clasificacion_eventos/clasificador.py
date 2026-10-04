# -*- coding: utf-8 -*-
"""
clasificador.py
===============

QUÉ HACE
--------
Aísla el MOTOR de clasificación de eventos detrás de una sola interfaz. Soporta
dos motores intercambiables (según config_eventos.MOTOR):
  - 'groq'  → le pregunta a un LLM en la nube (Llama 3.3 70B) vía API. Rápido y
              preciso; corre en el hardware de Groq, no en tu PC.
  - 'local' → modelo zero-shot de Hugging Face en tu PC. Respaldo sin internet.

PARA QUÉ SIRVE
--------------
Que los tres scripts del módulo (prueba_minima, validacion, clasificar_eventos)
usen la MISMA función `clasificar(texto)` sin saber qué motor hay debajo. Cambiar
de Groq a local (o al revés) es cambiar una línea en config_eventos, no el código.

DECISIÓN DE DISEÑO (defendible)
-------------------------------
Separar el motor de la lógica permite: (1) comparar motores con la misma tubería,
(2) cambiar de proveedor si Groq quita el modelo gratuito, y (3) no acoplar el
resto del código a un proveedor específico. Es el mismo principio de aislar el
acceso a datos que se usó en Gold.
"""

from __future__ import annotations

import os
import re
import sys
import json

import config_eventos as cfg


# ===========================================================================
# CARGA DE LA API KEY DE GROQ
# ===========================================================================
def _groq_key() -> str:
    """QUÉ HACE: obtiene la API key de Groq del entorno o de local.settings.json.
    PARA QUÉ SIRVE: no hardcodear la llave en el código (seguridad). La busca igual
    que la cadena de Azure: variable de entorno primero, luego local.settings.json."""
    key = os.environ.get(cfg.GROQ_KEY_ENV)
    if key:
        return key
    rutas = [
        "local.settings.json",
        os.path.join("..", "local.settings.json"),
        os.path.join("..", "..", "local.settings.json"),
        os.path.join("..", "..", "..", "local.settings.json"),
    ]
    for ruta in rutas:
        if os.path.exists(ruta):
            with open(ruta, "r", encoding="utf-8") as f:
                vals = json.load(f).get("Values", {})
            if vals.get(cfg.GROQ_KEY_ENV):
                return vals[cfg.GROQ_KEY_ENV]
    print(f"No encontré {cfg.GROQ_KEY_ENV}. Ponla en local.settings.json (bloque "
          f"'Values') o como variable de entorno.")
    sys.exit(1)


# ===========================================================================
# MOTOR GROQ (nube)
# ===========================================================================
class _MotorGroq:
    """QUÉ HACE: clasifica un texto preguntándole a un LLM de Groq.
    PARA QUÉ SIRVE: inferencia rápida y con contexto, sin cargar tu PC."""

    def __init__(self) -> None:
        try:
            from groq import Groq
        except ImportError:
            print("Falta la librería de Groq. Instala:  pip install groq")
            sys.exit(1)
        self.cliente = Groq(api_key=_groq_key())
        self.modelo = cfg.MODELO_GROQ
        # Lista de etiquetas para el prompt, con su descripción (ayuda al modelo).
        self.lista_etiquetas = "\n".join(
            f"- {e}: {cfg.DESCRIPCION_ETIQUETAS.get(e, '')}" for e in cfg.ETIQUETAS
        )

    def _prompt(self, texto: str) -> str:
        return (
            "Eres un clasificador de eventos de negocio en noticias del sector "
            "tecnológico. Lee la noticia y decide a CUÁL de estas categorías "
            "corresponde el evento principal. Responde SOLO con el nombre exacto "
            "de la categoría, sin explicaciones.\n\n"
            f"Categorías:\n{self.lista_etiquetas}\n\n"
            "Reglas: si la noticia no describe un evento de negocio claro (por "
            "ejemplo, una conmemoración, un artículo técnico general o una nota de "
            "opinión), responde 'otro_sin_evento'.\n\n"
            f"Noticia:\n\"\"\"\n{texto}\n\"\"\"\n\n"
            "Categoría:"
        )

    def clasificar(self, texto: str) -> tuple[str, float]:
        """Devuelve (etiqueta, confianza). Groq no da score numérico, así que la
        confianza es 1.0 si la respuesta es una etiqueta válida, 0.0 si no."""
        resp = self.cliente.chat.completions.create(
            model=self.modelo,
            messages=[{"role": "user", "content": self._prompt(texto)}],
            temperature=0,      # determinista: mismo texto → misma etiqueta
            max_tokens=15,      # solo necesitamos el nombre de la categoría
        )
        salida = (resp.choices[0].message.content or "").strip().lower()
        # Normaliza: busca cuál etiqueta válida aparece en la respuesta
        for e in cfg.ETIQUETAS:
            if e in salida:
                return e, 1.0
        # Si respondió algo no esperado, lo tratamos como sin evento
        return "otro_sin_evento", 0.0


# ===========================================================================
# MOTOR LOCAL (Hugging Face en tu PC)
# ===========================================================================
class _MotorLocal:
    """QUÉ HACE: clasifica con el modelo zero-shot de Hugging Face en tu PC.
    PARA QUÉ SIRVE: respaldo sin internet. Lento en hardware modesto."""

    def __init__(self) -> None:
        try:
            from transformers import pipeline
        except ImportError:
            print("Falta transformers/torch. Instala:  pip install transformers torch")
            sys.exit(1)
        self.pipe = pipeline("zero-shot-classification", model=cfg.MODELO_LOCAL)

    def clasificar(self, texto: str) -> tuple[str, float]:
        r = self.pipe(texto, cfg.ETIQUETAS,
                      hypothesis_template=cfg.HIPOTESIS, multi_label=False)
        etiqueta, score = r["labels"][0], float(r["scores"][0])
        if score < cfg.UMBRAL_CONFIANZA:
            return "otro_sin_evento", score
        return etiqueta, score


# ===========================================================================
# FÁBRICA: devuelve el motor configurado
# ===========================================================================
_motor_cache = None


def obtener_motor():
    """QUÉ HACE: crea (una sola vez) el motor según config_eventos.MOTOR.
    PARA QUÉ SIRVE: que los scripts pidan el motor sin saber cuál es. Cachea para
    no recargar el modelo/cliente en cada llamada."""
    global _motor_cache
    if _motor_cache is None:
        if cfg.MOTOR == "groq":
            _motor_cache = _MotorGroq()
        elif cfg.MOTOR == "local":
            _motor_cache = _MotorLocal()
        else:
            raise ValueError(f"MOTOR desconocido: '{cfg.MOTOR}'. Usa 'groq' o 'local'.")
    return _motor_cache