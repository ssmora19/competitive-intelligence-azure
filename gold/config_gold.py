# -*- coding: utf-8 -*-
"""
config_gold.py
==============

QUÉ HACE
--------
Define, de forma declarativa, la parametrización de los 8 contenedores de la
capa Gold: qué indicadores calcula cada uno, de qué señal salen, QUÉ NATURALEZA
TEMPORAL tiene cada señal (tendencia / vencimiento / estado / caducidad blanda),
qué veredicto accionable emite el contenedor, con qué pesos y umbrales, y qué
queda fuera de alcance.

PARA QUÉ SIRVE
--------------
Es el único lugar donde se configuran las 8 aplicaciones de Gold. Los motores
(agregacion.py, scoring.py) son genéricos y NO contienen lógica por dominio:
leen todo desde aquí, incluida la naturaleza temporal, que decide si una señal
se consolida por trimestre, se alerta por urgencia o se sigue como estado vigente.

PRINCIPIOS DE DISEÑO (para la sustentación)
-------------------------------------------
1. Transparencia total: scoring por reglas ponderadas explícitas, no ML de caja
   negra. Cada peso se justifica con una frase (ver comentarios).
2. Trazabilidad: cada indicador declara el KIN que responde.
3. Alcance explícito: lo que Bronze no captura (interno / HUMINT / financiero)
   se declara aquí como fuera de alcance, no se oculta.
4. NATURALEZA TEMPORAL: no todas las señales viven igual en el tiempo. Un
   lanzamiento (tendencia) se cuenta por trimestre; una licitación (vencimiento)
   se alerta por urgencia y NO espera al cierre del trimestre; un contrato
   vigente (estado) se sigue por nivel y su fin dispara oportunidad; una
   necesidad (caducidad blanda) decae y pondera reciencia. Tratarlas igual sería
   el error de fondo: consolidar una licitación por trimestre haría que TAK la
   perdiera por vencimiento.
5. Silver es el insumo: Gold NO reclasifica. Hereda 'tipo_senal', 'sentimiento',
   'relevancia', 'entidad', 'fecha_evento' (y 'fecha_cierre' donde aplique).

NOTA SOBRE LOS PESOS
--------------------
Los valores de 'pesos' y 'umbrales' son una PROPUESTA DE PARTIDA razonada, no
verdades absolutas. Son las perillas que tú ajustas y justificas. Cada uno lleva
su racional en comentario. La estructura no cambia si cambias los números.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# TABLAS GOLD (Azure Table Storage)
# ---------------------------------------------------------------------------
TABLA_INDICADORES = "goldIndicadores"      # cuantitativo → Gerencia
TABLA_VEREDICTOS = "goldVeredictos"        # accionable → Ventas
TABLA_ALERTAS = "goldAlertas"              # oportunidades/obligaciones con vencimiento → Ventas (urgente)
TABLA_COBERTURA = "goldCoberturaKIN"       # cobertura → jurado / trazabilidad
TABLA_LOGS = "executionLogsGold"           # auditoría de ejecución

# ---------------------------------------------------------------------------
# NATURALEZA TEMPORAL DE LAS SEÑALES
# ---------------------------------------------------------------------------
# Este es el corazón de la actualización. Cada señal declara una de estas cuatro
# naturalezas, y el motor decide su tratamiento en función de ella.
#
#   consolida_por_periodo = True  → se agrupa por trimestre y se calcula variación %
#   consolida_por_periodo = False → NO se mete en cubos trimestrales
#
NATURALEZAS = {
    "tendencia": {
        "desc": "Hecho puntual que se acumula. Interesa el patrón en el tiempo.",
        "tratamiento": "Consolidar por periodo (trimestre) + variación % vs periodo anterior.",
        "consolida_por_periodo": True,
        "genera_alerta": False,
    },
    "vencimiento": {
        "desc": ("Oportunidad u obligación con fecha de cierre. Caduca; si la "
                 "detección llega tarde, vale cero (ej. licitación, convocatoria)."),
        "tratamiento": ("NO consolidar por periodo. Emitir ALERTA priorizada por "
                        "urgencia (días restantes hasta fecha_cierre)."),
        "consolida_por_periodo": False,
        "genera_alerta": True,
    },
    "estado": {
        "desc": ("Condición vigente AHORA (stock), persiste hasta que cambia. "
                 "Su terminación puede ser una oportunidad (ej. contrato vigente)."),
        "tratamiento": ("Reportar nivel vigente actual (no flujo). Vigilar la "
                        "terminación como disparador de oportunidad."),
        "consolida_por_periodo": False,
        "genera_alerta": False,
    },
    "caducidad_blanda": {
        "desc": ("Interés o necesidad que no vence de golpe pero decae. "
                 "Accionable mientras es reciente (ej. necesidad tecnológica)."),
        "tratamiento": ("Contar como tendencia, pero la reciencia pondera fuerte "
                        "en el scoring (ventana de frescura)."),
        "consolida_por_periodo": True,
        "genera_alerta": False,
    },
}

# ---------------------------------------------------------------------------
# STACK TECNOLÓGICO DE TAK (factor "coincidencia con lo que TAK domina")
# ---------------------------------------------------------------------------
STACK_TAK = {
    "oracle", "aws", "amazon web services", "microsoft", "azure", "ibm",
    "dell", "fortinet", "red hat", "redhat", "td synnex", "nexsys",
}

NIVELES = ("alto", "medio", "bajo")

# ---------------------------------------------------------------------------
# VENTANA TEMPORAL DE ANÁLISIS
# ---------------------------------------------------------------------------
# Gold agrega por fecha_evento (cuándo ocurrió el hecho). Algunos registros traen
# fechas muy viejas (2020, 2022…) que ensucian las tendencias recientes. Este
# corte descarta eventos anteriores al año indicado. Ajusta según el periodo que
# quieras analizar. Los registros sin fecha (cubo 'sin_fecha') NO se ven afectados.
ANIO_MINIMO = 2025

# ===========================================================================
# PARAMETRIZACIÓN DE LOS 8 CONTENEDORES
# ===========================================================================
# Cada indicador ahora declara:
#   tipo_senal (o 'modo')  : qué se cuenta / cómo se agrega
#   naturaleza             : una clave de NATURALEZAS (decide el tratamiento)
#   desc                   : descripción legible (para la tabla de indicadores)
#   requiere_fecha_cierre  : True si la señal necesita fecha_cierre de Silver
#   obligacion_con_plazo   : True si además dispara alerta cuando trae plazo
# ===========================================================================

CONTENEDORES_GOLD: dict[str, dict] = {

    # -----------------------------------------------------------------------
    # 1. COMPETIDORES  →  veredicto: nivel de amenaza / actividad
    #    Todas las señales son de TENDENCIA: interesa el patrón trimestral.
    # -----------------------------------------------------------------------
    "competidores": {
        "kin_id": "KIN-1",
        "kin_nombre": "Estrategias y posicionamiento competitivo",
        "silver_container": "silver-competidores",
        "veredicto": "nivel_amenaza",
        "indicadores": {
            "n_lanzamientos": {"tipo_senal": "lanzamiento", "naturaleza": "tendencia",
                "desc": "Número de lanzamientos por competidor"},
            "frecuencia_productos": {"tipo_senal": "lanzamiento", "modo": "frecuencia",
                "naturaleza": "tendencia",
                "desc": "Frecuencia de nuevos productos en el mercado"},
            "actividad_marketing": {"tipo_senal": "contenido_corporativo", "naturaleza": "tendencia",
                "desc": "Nivel de actividad comercial y marketing"},
            "n_alianzas": {"tipo_senal": "alianza", "naturaleza": "tendencia",
                "desc": "Número de alianzas estratégicas"},
            "actividad_tecnica": {"tipo_senal": "actividad_tecnica", "naturaleza": "tendencia",
                "desc": "Actividad técnica y desarrollo"},
            "n_expansion": {"tipo_senal": "expansion", "naturaleza": "tendencia",
                "desc": "Expansión a nuevos mercados o sectores"},
        },
        "scoring": {
            "factores": {
                "volumen_señales": 0.35,
                "diversidad_tipos": 0.30,
                "reciencia": 0.20,
                "relevancia_promedio": 0.15,
            },
            "umbrales": {"alto": 0.66, "medio": 0.33},
        },
        "fuera_de_alcance": {},
    },

    # -----------------------------------------------------------------------
    # 2. CLIENTES  →  veredicto: lead score (expansión)
    #    Necesidad = CADUCIDAD BLANDA (decae). Segmento = ESTADO.
    # -----------------------------------------------------------------------
    "clientes": {
        "kin_id": "KIN-2",
        "kin_nombre": "Comportamiento y necesidades de los clientes",
        "silver_container": "silver-clientes",
        "veredicto": "lead_score",
        "indicadores": {
            "n_necesidades": {"tipo_senal": "demanda_talento", "naturaleza": "caducidad_blanda",
                "desc": "Número de necesidades identificadas (demanda de talento/tecnología)"},
            "n_contratacion": {"tipo_senal": "contratacion_publica", "naturaleza": "tendencia",
                "desc": "Contratación pública detectada (SECOP)"},
        },
        "scoring": {
            "factores": {
                "n_necesidades": 0.30,
                "coincidencia_stack": 0.40,   # ventaja de cierre real (TAK partner)
                "reciencia": 0.20,            # una necesidad vieja ya no es lead
                "relevancia_promedio": 0.10,
            },
            "umbrales": {"alto": 0.60, "medio": 0.30},
        },
        "fuera_de_alcance": {
            "criterios_seleccion": "HUMINT (entrevistas). Integración futura.",
            "nivel_satisfaccion": "CRM/encuestas (Zoho). Integración futura.",
            "madurez_digital": "Evaluación interna/HUMINT. Integración futura.",
            "tasa_conversion": "CRM (dato de cierre de venta). Integración futura.",
        },
    },

    # -----------------------------------------------------------------------
    # 3. CLIENTES-COMPETENCIA  →  veredicto: oportunidad de captura
    #    contrato_vigente = ESTADO cuyo FIN es la mejor oportunidad de captura.
    # -----------------------------------------------------------------------
    "clientes-competencia": {
        "kin_id": "KIN-8",
        "kin_nombre": "Clientes de la competencia (oportunidades de captura)",
        "silver_container": "silver-clientes-competencia",
        "veredicto": "oportunidad_captura",
        "indicadores": {
            "n_clientes_competencia": {"modo": "conteo_entidades", "naturaleza": "estado",
                "desc": "Número de clientes de competencia identificados (vigentes)"},
            "n_contratacion": {"tipo_senal": "contratacion_publica", "naturaleza": "tendencia",
                "desc": "Contratación pública de clientes de competencia (SECOP)"},
            "n_alianzas": {"tipo_senal": "alianza", "naturaleza": "tendencia",
                "desc": "Alianzas detectadas en clientes de competencia"},
            "n_oportunidades_captura": {"modo": "scoring", "veredicto_nivel": "alto",
                "naturaleza": "estado",
                "desc": "Número de oportunidades de captura priorizadas"},
        },
        "scoring": {
            "factores": {
                "coincidencia_stack": 0.45,
                "n_necesidades": 0.25,
                "reciencia": 0.30,
            },
            "umbrales": {"alto": 0.60, "medio": 0.30},
        },
        "fuera_de_alcance": {},
    },

    # -----------------------------------------------------------------------
    # 4. TENDENCIAS  →  veredicto: relevancia tecnológica para TAK
    #    Todas TENDENCIA.
    # -----------------------------------------------------------------------
    "tendencias": {
        "kin_id": "KIN-3",
        "kin_nombre": "Tecnología e innovación",
        "silver_container": "silver-tendencias",
        "veredicto": "relevancia_tecnologica",
        "indicadores": {
            "n_actividad_tecnica": {"tipo_senal": "actividad_tecnica", "naturaleza": "tendencia",
                "desc": "Actividad técnica y tecnologías emergentes detectadas"},
            "n_lanzamientos_tec": {"tipo_senal": "lanzamiento", "naturaleza": "tendencia",
                "desc": "Lanzamientos tecnológicos"},
            "n_referencias": {"tipo_senal": "referencia_web", "modo": "agrupacion",
                "naturaleza": "tendencia",
                "desc": "Referencias tecnológicas en la web"},
        },
        "scoring": {
            "factores": {
                "coincidencia_stack": 0.40,
                "traccion": 0.35,
                "reciencia": 0.25,
            },
            "umbrales": {"alto": 0.60, "medio": 0.30},
        },
        "fuera_de_alcance": {
            "nivel_inversion_tecnologias": "Datos financieros. Integración futura.",
        },
    },

    # -----------------------------------------------------------------------
    # 5. PROVEEDORES  →  veredicto: alerta de cambio
    #    cambio_licenciamiento = TENDENCIA que además ALERTA si toca el stack.
    # -----------------------------------------------------------------------
    "proveedores": {
        "kin_id": "KIN-4",
        "kin_nombre": "Ecosistema de proveedores tecnológicos",
        "silver_container": "silver-proveedores",
        "veredicto": "alerta_cambio",
        "indicadores": {
            "n_referencias": {"tipo_senal": "referencia_web", "naturaleza": "tendencia",
                "desc": "Referencias y novedades de proveedores en la web"},
            "n_alianzas": {"tipo_senal": "alianza", "naturaleza": "tendencia",
                "alerta_si_stack": True,   # si toca un proveedor del stack de TAK, alerta
                "desc": "Alianzas tecnológicas de proveedores"},
            "n_inversion": {"tipo_senal": "inversion", "naturaleza": "tendencia",
                "desc": "Movimientos de inversión de proveedores"},
        },
        "scoring": {
            "factores": {
                "coincidencia_stack": 0.45,
                "reciencia": 0.30,
                "relevancia_promedio": 0.25,
            },
            "umbrales": {"alto": 0.60, "medio": 0.30},
        },
        "fuera_de_alcance": {},
    },

    # -----------------------------------------------------------------------
    # 6. MERCADO  →  veredicto: oportunidad de mercado
    #    licitacion = VENCIMIENTO (¡lo que salvó Mike!). oportunidad_expansion
    #    = vencimiento si trae plazo, si no cae a tendencia (fallback en motor).
    # -----------------------------------------------------------------------
    "mercado": {
        "kin_id": "KIN-5",
        "kin_nombre": "Comportamiento del mercado tecnológico",
        "silver_container": "silver-mercado",
        "veredicto": "oportunidad_mercado",
        "indicadores": {
            "n_contratacion": {"tipo_senal": "contratacion_publica", "naturaleza": "tendencia",
                "desc": "Contratación pública / oportunidades detectadas (SECOP)"},
            "n_inversion": {"tipo_senal": "inversion", "naturaleza": "tendencia",
                "desc": "Movimientos de inversión en el mercado"},
            "n_expansion": {"tipo_senal": "expansion", "naturaleza": "tendencia",
                "desc": "Señales de expansión de mercado"},
        },
        "scoring": {
            "factores": {
                "coincidencia_stack": 0.40,
                "reciencia": 0.35,
                "relevancia_promedio": 0.25,
            },
            "umbrales": {"alto": 0.60, "medio": 0.30},
        },
        "fuera_de_alcance": {
            "tasa_crecimiento_segmento": "Parcial: requiere series DANE. Cobertura limitada.",
        },
    },

    # -----------------------------------------------------------------------
    # 7. REGULATORIO  →  veredicto: alerta regulatoria / financiación
    #    convocatoria_financiacion = VENCIMIENTO (igual que licitaciones).
    #    regulacion = TENDENCIA + obligación con plazo (alerta si trae fecha).
    # -----------------------------------------------------------------------
    "regulatorio": {
        "kin_id": "KIN-6",
        "kin_nombre": "Entorno regulatorio y financiero tecnológico",
        "silver_container": "silver-regulatorio",
        "veredicto": "alerta_regulatoria",
        "indicadores": {
            "n_regulaciones": {"tipo_senal": "regulatorio", "naturaleza": "tendencia",
                "desc": "Número de regulaciones relevantes"},
            "n_inversion": {"tipo_senal": "inversion", "naturaleza": "tendencia",
                "desc": "Inversión pública / programas detectados"},
            "n_referencias": {"tipo_senal": "referencia_web", "naturaleza": "tendencia",
                "desc": "Referencias normativas en la web"},
        },
        "scoring": {
            "factores": {
                "relevancia_promedio": 0.45,  # ¿aplica realmente a TAK?
                "reciencia": 0.35,
                "volumen_señales": 0.20,
            },
            "umbrales": {"alto": 0.60, "medio": 0.30},
        },
        "fuera_de_alcance": {
            "nivel_inversion_sector": "Datos financieros. Integración futura.",
            "n_barreras_financieras": "HUMINT/financiero. Integración futura.",
        },
    },

    # -----------------------------------------------------------------------
    # 8. CAPACIDADES  →  veredicto: gap de capacidad (frente EXTERNO)
    #    Demanda/certificación = TENDENCIA. Brecha vs stack = ESTADO (condición
    #    vigente: hoy TAK tiene o no tiene esa capacidad).
    # -----------------------------------------------------------------------
    "capacidades": {
        "kin_id": "KIN-7",
        "kin_nombre": "Capacidades internas y talento humano (frente externo: brecha)",
        "silver_container": "silver-capacidades",
        "veredicto": "gap_capacidad",
        "indicadores": {
            "n_tecnologias_demandadas": {"tipo_senal": "demanda_talento", "naturaleza": "tendencia",
                "desc": "Tecnologías/perfiles demandados por el mercado"},
            "n_referencias": {"tipo_senal": "referencia_web", "naturaleza": "tendencia",
                "desc": "Referencias de capacidades (benchmarking)"},
            "n_brecha_competencia": {"tipo_senal": "demanda_talento", "modo": "brecha_stack",
                "naturaleza": "estado",
                "desc": "Tecnologías demandadas que TAK no reporta (brecha vigente)"},
        },
        "scoring": {
            "factores": {
                "volumen_señales": 0.45,      # cuán demandado por el mercado
                "brecha_vs_stack": 0.35,      # NO está en stack TAK → gap real
                "reciencia": 0.20,
            },
            "umbrales": {"alto": 0.60, "medio": 0.30},
        },
        "fuera_de_alcance": {
            "capacidades_tecnicas_equipo": "Frente INTERNO. Evaluación RRHH. Integración futura.",
            "n_tecnologias_adoptadas_interno": "Frente INTERNO. Inventario interno. Integración futura.",
            "tiempo_adaptacion_cambio": "Frente INTERNO. Evaluación RRHH. Integración futura.",
        },
    },
}


# ---------------------------------------------------------------------------
# HELPERS DE CONSULTA
# ---------------------------------------------------------------------------
def contenedores() -> list[str]:
    """QUÉ HACE: lista los 8 contenedores Gold.
    PARA QUÉ SIRVE: iterar sobre todos los dominios en el orquestador."""
    return list(CONTENEDORES_GOLD.keys())


def config(contenedor: str) -> dict:
    """QUÉ HACE: devuelve la configuración completa de un contenedor.
    PARA QUÉ SIRVE: que los motores lean su parametrización sin hardcodear nada."""
    if contenedor not in CONTENEDORES_GOLD:
        raise KeyError(f"Contenedor '{contenedor}' no existe. Válidos: {contenedores()}")
    return CONTENEDORES_GOLD[contenedor]


def indicadores_alimentables(contenedor: str) -> dict:
    """QUÉ HACE: devuelve los indicadores que Gold SÍ calcula.
    PARA QUÉ SIRVE: el motor de agregación itera solo sobre lo alimentable."""
    return config(contenedor).get("indicadores", {})


def indicadores_por_naturaleza(contenedor: str, naturaleza: str) -> dict:
    """
    QUÉ HACE: filtra los indicadores de un contenedor por naturaleza temporal.
    PARA QUÉ SIRVE: que agregacion.py pida "dame los de tendencia" (para
    consolidar por trimestre) por separado de los de "vencimiento" (para alertar).
    """
    return {
        nombre: ind
        for nombre, ind in indicadores_alimentables(contenedor).items()
        if ind.get("naturaleza") == naturaleza
    }


def consolida_por_periodo(indicador: dict) -> bool:
    """QUÉ HACE: dice si un indicador se agrupa por trimestre.
    PARA QUÉ SIRVE: el motor decide, señal por señal, si va a cubos trimestrales
    o si se trata como alerta/estado. Respeta 'fallback_naturaleza' si aplica."""
    nat = indicador.get("naturaleza", "tendencia")
    return NATURALEZAS.get(nat, {}).get("consolida_por_periodo", True)


def genera_alerta(indicador: dict) -> bool:
    """QUÉ HACE: dice si un indicador debe emitir alerta de vencimiento.
    PARA QUÉ SIRVE: el motor enruta a goldAlertas las señales con fecha de cierre."""
    nat = indicador.get("naturaleza", "tendencia")
    if NATURALEZAS.get(nat, {}).get("genera_alerta", False):
        return True
    # Casos híbridos: tendencia que también alerta bajo condición.
    return bool(indicador.get("obligacion_con_plazo") or indicador.get("alerta_si_stack"))


def senales_con_vencimiento() -> list[tuple[str, str]]:
    """QUÉ HACE: lista (contenedor, indicador) de todo lo que requiere fecha_cierre.
    PARA QUÉ SIRVE: verificar contra Silver que esos registros traigan la fecha;
    si no la traen, la alerta de urgencia no se puede calcular."""
    out = []
    for c in contenedores():
        for nombre, ind in indicadores_alimentables(c).items():
            if ind.get("requiere_fecha_cierre"):
                out.append((c, nombre))
    return out


def resumen_cobertura() -> dict:
    """QUÉ HACE: cuenta indicadores alimentables vs fuera de alcance por KIN.
    PARA QUÉ SIRVE: alimenta la tabla de cobertura KIN (evidencia para jurado)."""
    resumen = {}
    for c, cfg in CONTENEDORES_GOLD.items():
        alim = len(cfg.get("indicadores", {}))
        fuera = len(cfg.get("fuera_de_alcance", {}))
        resumen[c] = {
            "kin_id": cfg["kin_id"],
            "alimentables": alim,
            "fuera_de_alcance": fuera,
            "cobertura_pct": round(100 * alim / (alim + fuera), 1) if (alim + fuera) else 0.0,
        }
    return resumen


def validar_config() -> list[str]:
    """
    QUÉ HACE: revisa la coherencia de la config y devuelve una lista de avisos.
    PARA QUÉ SIRVE: atrapar errores de configuración antes de correr Gold — por
    ejemplo, una señal de 'vencimiento' sin 'requiere_fecha_cierre', o una
    naturaleza inexistente. Se corre al inicio del orquestador.
    """
    avisos: list[str] = []
    for c in contenedores():
        for nombre, ind in indicadores_alimentables(c).items():
            nat = ind.get("naturaleza")
            if nat is None:
                avisos.append(f"[{c}.{nombre}] sin 'naturaleza' declarada.")
            elif nat not in NATURALEZAS:
                avisos.append(f"[{c}.{nombre}] naturaleza '{nat}' no existe en NATURALEZAS.")
            if nat == "vencimiento" and not ind.get("requiere_fecha_cierre"):
                avisos.append(f"[{c}.{nombre}] es 'vencimiento' pero no exige fecha_cierre.")
        cfg = config(c)
        pesos = cfg.get("scoring", {}).get("factores", {})
        suma = round(sum(pesos.values()), 4) if pesos else 0
        if pesos and abs(suma - 1.0) > 0.001:
            avisos.append(f"[{c}] los pesos de scoring suman {suma}, no 1.0.")
    return avisos