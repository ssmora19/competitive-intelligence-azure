# -*- coding: utf-8 -*-
"""
pipeline.py
===========

QUÉ HACE
--------
Orquesta, para UN blob de Bronze, toda la transformación Silver: detecta familia,
normaliza, aplica las funciones de calidad, enriquece y ensambla el registro
canónico final. No toca Azure: entra un dict (el blob) y sale un dict (el canónico).

PARA QUÉ SIRVE
--------------
Separa la LÓGICA de Silver de la INFRAESTRUCTURA (Azure). Esto permite probar el
comportamiento con los mismos JSON que ya descargaste, sin desplegar nada — que
es justo lo que da solidez en la sustentación: "puedo demostrar cada regla con un
caso real, de forma reproducible y offline".

FLUJO (y la dimensión de calidad que atiende cada paso)
-------------------------------------------------------
1. Familia            (CONSISTENCIA)  -> dispatcher.resolver_familia
2. Normalización      (CONSISTENCIA + VALIDEZ) -> normalizadores.REGISTRO
3. Entidad            (EXACTITUD)      -> calidad.resolver_entidad
4. Completitud        (COMPLETITUD)    -> calidad.evaluar_completitud
5. Vigencia           (VIGENCIA)       -> calidad.es_vigente
6. Unicidad           (UNICIDAD)       -> calidad.hash_contenido
7. Clasificación      (enriquecimiento)-> enriquecimiento.clasificar
8. Relevancia         (RELEVANCIA)     -> enriquecimiento.puntuar_relevancia
9. Sentimiento (opc.) (enriquecimiento)-> enriquecimiento.estimar_sentimiento
10. Prioridad         (operación)      -> config.prioridad_de
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config_silver as cfg
from . import calidad
from . import dispatcher
from . import normalizadores
from . import enriquecimiento

# Familias donde el dato no representa a una entidad-autor concreta: la noticia
# es una mención de terceros y la página web es un índice. En ellas la señal se
# rastrea por término de búsqueda + relevancia, no por confirmación de entidad,
# así que no se aplican las banderas de exactitud de entidad.
FAMILIAS_SIN_ENTIDAD = {"noticia_rss", "web_firecrawl"}


class ResultadoSilver:
    """
    QUÉ HACE: envuelve el resultado de procesar un blob (canónico + estado).
    PARA QUÉ SIRVE: el orquestador de Azure necesita saber si escribir, saltar o
    reportar error, sin volver a inspeccionar el dato.
    Estados: 'ok' | 'sin_familia' | 'error'.
    """
    __slots__ = ("estado", "canonico", "familia", "motivo")

    def __init__(self, estado, canonico=None, familia=None, motivo=None):
        self.estado = estado
        self.canonico = canonico
        self.familia = familia
        self.motivo = motivo


def procesar(blob: dict, ahora: datetime | None = None) -> ResultadoSilver:
    """
    QUÉ HACE: transforma un blob Bronze completo ({_meta, raw}) en un registro
    canónico Silver validado y enriquecido.
    PARA QUÉ SIRVE: es la función central de la capa. Todo lo demás la alimenta o
    la consume.
    """
    ahora = ahora or datetime.now(timezone.utc)
    meta = blob.get("_meta") or {}
    raw = blob.get("raw") or {}

    # --- 1. Familia (por estructura, no por nombre) ---
    familia = dispatcher.resolver_familia(raw, meta.get("fuente", ""))
    if not familia:
        return ResultadoSilver("sin_familia", familia=None, motivo="estructura no reconocida")

    normalizador = normalizadores.REGISTRO.get(familia)
    if not normalizador:
        return ResultadoSilver("sin_familia", familia=familia, motivo="familia sin normalizador")

    try:
        # --- 2. Normalización a campos canónicos ---
        n = normalizador(raw, meta)

        # --- 3. Entidad (EXACTITUD): autor del contenido manda; flag si difiere ---
        flags = list(n.get("flags", []))
        if familia in FAMILIAS_SIN_ENTIDAD:
            # Noticias y web no monitorean una entidad-autor; la señal se rastrea
            # por término de búsqueda + relevancia, no por confirmación de entidad.
            entidad = (n["entidad_contenido"] or "").strip()
        else:
            entidad, flag_ent = calidad.resolver_entidad(n["entidad_declarada"], n["entidad_contenido"])
            if flag_ent:
                flags.append(flag_ent)

        # --- Ensamble del esqueleto canónico (identidad + trazabilidad) ---
        canonico = {
            # Identidad / trazabilidad
            "id": meta.get("row_key"),
            "item_id_origen": meta.get("item_id"),
            "fuente_original": meta.get("fuente"),
            "familia": familia,
            "variable_ic": meta.get("variable_ic"),
            "ingest_ts_utc": meta.get("ingest_ts_utc"),
            "procesado_ts_utc": ahora.isoformat(),
            "schema_silver": cfg.SCHEMA_SILVER_VERSION,

            # Entidad
            "entidad_monitoreada": entidad,
            "entidad_declarada": calidad.limpiar_entidad_declarada(n["entidad_declarada"]),
            "termino_busqueda": n.get("termino_busqueda"),
            "origen_dato": n.get("origen_dato"),

            # Contenido canónico
            "titulo": n["titulo"],
            "descripcion": n["descripcion"],
            "fecha_evento_utc": n["fecha_evento_utc"],
            "url": n["url"],
            "idioma": n.get("idioma"),

            # Bloque estructurado propio de la familia
            (n["bloque_nombre"] or "extra"): n["bloque"] if n["bloque_nombre"] else None,

            # Operación (énfasis del cliente como peso configurable)
            "prioridad_operativa": cfg.prioridad_de(familia),
        }

        # --- 4. Completitud (COMPLETITUD) ---
        faltantes = calidad.evaluar_completitud(canonico)

        # --- 5. Vigencia (VIGENCIA) ---
        ventana = cfg.ventana_recencia_de(familia)
        vigente = calidad.es_vigente(canonico["fecha_evento_utc"], ventana, ahora=ahora)

        # --- 6. Unicidad (UNICIDAD): hash de contenido para dedupe cross-source ---
        hpartes = n.get("hash_partes") or (canonico["titulo"], canonico["url"])
        h = calidad.hash_contenido(*hpartes)

        # Bloque de calidad consolidado (sin relevancia todavía; se añade abajo).
        canonico["calidad"] = {
            "campos_faltantes": faltantes,
            "flags": flags,
        }
        canonico["vigencia"] = {"vigente": vigente, "ventana_dias": ventana}
        canonico["hash_contenido"] = h

        # --- 7. Clasificación (enriquecimiento) ---
        canonico["clasificacion"] = enriquecimiento.clasificar(canonico)

        # --- 8. Relevancia (RELEVANCIA) ---
        canonico["relevancia"] = enriquecimiento.puntuar_relevancia(canonico)

        # --- 9. Sentimiento (opcional) ---
        canonico["sentimiento"] = enriquecimiento.estimar_sentimiento(canonico)

        return ResultadoSilver("ok", canonico=canonico, familia=familia)

    except Exception as exc:  # noqa: BLE001  -> se reporta, no se cae la corrida completa
        return ResultadoSilver("error", familia=familia, motivo=f"{type(exc).__name__}: {exc}")