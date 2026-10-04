# -*- coding: utf-8 -*-
"""Verifica si las cifras en goldCifrasMercado tienen fuente y fecha."""
import os, sys, json

def _conn():
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if env: return env
    for r in ("local.settings.json", os.path.join("..","local.settings.json")):
        if os.path.exists(r):
            v = json.load(open(r,encoding="utf-8")).get("Values",{})
            if v.get("AZURE_STORAGE_CONNECTION_STRING"): return v["AZURE_STORAGE_CONNECTION_STRING"]
    sys.exit("No hay conexión")

from azure.data.tables import TableServiceClient
t = TableServiceClient.from_connection_string(_conn()).get_table_client("goldCifrasMercado")

for e in sorted(t.list_entities(), key=lambda x: int(x.get("orden",0))):
    print(f"Cifra: {e.get('valor','?')}")
    print(f"  etiqueta: {e.get('etiqueta','?')}")
    print(f"  fuente:   {repr(e.get('fuente','NO EXISTE'))}")
    print(f"  fecha:    {repr(e.get('fecha','NO EXISTE'))}")
    print()