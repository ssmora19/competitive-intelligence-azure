# -*- coding: utf-8 -*-
"""
entidades.py
============

QUÉ HACE
--------
Carga y consolida el universo de ENTIDADES MONITOREADAS por TAK a partir de los
tres archivos maestros del proyecto: competidores (companies.json), clientes
(clientes.json) y clientes de la competencia (clientes_competencia.json). Expone
una función para saber si un texto menciona alguna de esas entidades.

PARA QUÉ SIRVE
--------------
Es la base del filtro de relevancia mejorado. En vez de una lista fija escrita a
mano, la relevancia se decide contra las entidades REALES que TAK ya definió en
su ingesta. Si mañana se agrega un cliente, el filtro lo reconoce sin tocar
código. Las entidades inactivas se ignoran por decisión de negocio.

SUSTENTO (defensa)
------------------
"El filtro de relevancia no usa una lista arbitraria: se alimenta del mismo
universo de entidades que la organización definió como sus clientes,
competidores y clientes de la competencia. Es una fuente de verdad única y
dinámica, lo que hace el criterio de relevancia trazable y mantenible."
"""

from __future__ import annotations

import os
import re
import json
import unicodedata

# Rutas REALES de los archivos maestros dentro del proyecto. Los archivos NO se
# mueven ni se duplican: se leen donde el Bronze ya los tiene, para mantener una
# sola fuente de verdad y no romper la ingesta existente.
#   - competidores        -> data/companies.json
#   - clientes            -> ingestion_clientes/clientes.json
#   - clientes-competencia-> ingestion_clientes_competencia/clientes_competencia.json
RUTA_COMPETIDORES = os.path.join("data", "companies.json")
RUTA_CLIENTES = os.path.join("ingestion_clientes", "clientes.json")
RUTA_CLIENTES_COMP = os.path.join("ingestion_clientes_competencia", "clientes_competencia.json")

# Palabras muy genéricas que NO deben usarse como "nombre de entidad" para
# evitar falsos positivos (p.ej. que 'SA' o 'Colombia' disparen coincidencias).
_STOP_ENTIDAD = {
    "sa", "sas", "ltda", "inc", "corp", "corporation", "colombia", "co",
    "group", "grupo", "de", "del", "la", "el", "los", "las", "y", "s",
    "compania", "nacional", "the", "eps",
}


def _norm(t: str) -> str:
    """Minúsculas, sin acentos, sin puntuación."""
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _cargar_json(ruta: str):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def cargar_entidades(base_dir: str = ".") -> set:
    """
    QUÉ HACE: devuelve un conjunto de nombres de entidad monitoreada, normalizados,
    tomados de los tres archivos maestros EN SUS RUTAS REALES. Ignora inactivas.
    PARA QUÉ SIRVE: es la lista contra la que el filtro de relevancia compara.
    Lee los mismos archivos que usa el Bronze (fuente única, sin duplicar).
    El parámetro base_dir permite prefijar una carpeta base (para pruebas).
    """
    nombres = set()

    # 1) Competidores: data/companies.json  -> lista de dicts con 'nombre'
    for c in _cargar_json(os.path.join(base_dir, RUTA_COMPETIDORES)):
        if isinstance(c, dict) and c.get("nombre"):
            nombres.add(_norm(c["nombre"]))

    # 2) Clientes: ingestion_clientes/clientes.json -> 'nombre' + 'secop_nombres'
    for c in _cargar_json(os.path.join(base_dir, RUTA_CLIENTES)):
        if not isinstance(c, dict) or not c.get("activo", True):
            continue
        if c.get("nombre"):
            nombres.add(_norm(c["nombre"]))
        for sn in c.get("secop_nombres", []) or []:
            nombres.add(_norm(sn))

    # 3) Clientes de competencia: 'entidad' + 'nombre_secop'. Ignora inactivas.
    for c in _cargar_json(os.path.join(base_dir, RUTA_CLIENTES_COMP)):
        if not isinstance(c, dict) or not c.get("activo", True):
            continue  # <- inactivas ignoradas (decisión de negocio)
        if c.get("entidad"):
            nombres.add(_norm(c["entidad"]))
        if c.get("nombre_secop"):
            nombres.add(_norm(c["nombre_secop"]))

    # Limpieza: quitar entradas vacías o demasiado genéricas/cortas.
    limpio = set()
    for n in nombres:
        if not n or n in _STOP_ENTIDAD:
            continue
        if len(n) < 3:
            continue
        limpio.add(n)
    return limpio


def menciona_entidad(texto: str, entidades: set) -> str | None:
    """
    QUÉ HACE: indica si el texto menciona alguna entidad monitoreada; devuelve el
    nombre de la entidad encontrada o None.
    PARA QUÉ SIRVE: componente 1 del filtro de relevancia (Regla A: menciona
    entidad O es sector TI). Usa coincidencia por palabra completa para evitar
    que 'claro' (color) o subcadenas disparen falsos positivos... salvo que la
    entidad sea muy distintiva.
    """
    t = _norm(texto)
    if not t:
        return None
    t_pad = f" {t} "
    for ent in entidades:
        if not ent:
            continue
        # Coincidencia por frase completa rodeada de límites de palabra.
        if f" {ent} " in t_pad:
            return ent
    return None