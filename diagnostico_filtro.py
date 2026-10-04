# -*- coding: utf-8 -*-
"""
diagnostico_filtro.py
QUE HACE: audita el filtro de relevancia sobre la Silver real. Cuenta relevantes
vs ruido por contenedor, y saca una MUESTRA de lo descartado para revisar si el
filtro esta perdiendo senal valiosa. NO llama a Gemini, no modifica nada.
PARA QUE SIRVE: cuantificar y defender el filtro antes de procesar masivamente.
"""
import os, json, csv, random
from azure.storage.blob import BlobServiceClient

CONTENEDORES = ["silver-competidores","silver-clientes","silver-clientes-competencia",
    "silver-tendencias","silver-proveedores","silver-regulatorio","silver-mercado","silver-capacidades"]

def get_conn():
    c = os.environ.get("AzureWebJobsStorage","")
    if c: return c
    with open("local.settings.json",encoding="utf-8") as f:
        return json.load(f).get("Values",{}).get("AzureWebJobsStorage","")

bs = BlobServiceClient.from_connection_string(get_conn())

resumen = {}
descartados = []   # muestra de no-relevantes
relevantes_muestra = []

for cont in CONTENEDORES:
    cc = bs.get_container_client(cont)
    try:
        blobs = list(cc.list_blobs())
    except Exception as e:
        print(f"(no se pudo listar {cont})"); continue
    rel = ruido = 0
    for b in blobs:
        try:
            reg = json.loads(cc.get_blob_client(b.name).download_blob().readall())
        except Exception:
            continue
        pasa = reg.get("relevancia",{}).get("pasa_umbral", False)
        if pasa:
            rel += 1
            if len(relevantes_muestra) < 15:
                relevantes_muestra.append((cont, reg.get("titulo","")[:70],
                    reg.get("relevancia",{}).get("motivo","")))
        else:
            ruido += 1
            descartados.append((cont, reg.get("titulo","")[:70],
                reg.get("relevancia",{}).get("motivo","")))
    resumen[cont] = (rel, ruido)

print("="*70)
print("RESUMEN POR CONTENEDOR (relevantes / ruido)")
print("="*70)
tot_rel = tot_ruido = 0
for c,(r,n) in resumen.items():
    tot_rel += r; tot_ruido += n
    print(f"  {c:32} rel={r:5}  ruido={n:5}")
print(f"  {'TOTAL':32} rel={tot_rel:5}  ruido={tot_ruido:5}")
print(f"\n  A Gemini irian: {tot_rel} registros")
print(f"  Filtrados (no van): {tot_ruido} registros ({100*tot_ruido/(tot_rel+tot_ruido):.0f}% del total)")

print("\n" + "="*70)
print("MUESTRA DE 25 DESCARTADOS (¿es basura de verdad, o se pierde senal?)")
print("="*70)
random.seed(42)
for cont, tit, mot in random.sample(descartados, min(25, len(descartados))):
    print(f"  [{cont.replace('silver-','')[:12]:12}] {tit}")
    print(f"       motivo: {mot}")