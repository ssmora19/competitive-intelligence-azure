import os, json
from azure.storage.blob import BlobServiceClient
CONTENEDORES = ["silver-competidores","silver-clientes","silver-clientes-competencia",
    "silver-tendencias","silver-proveedores","silver-regulatorio","silver-mercado","silver-capacidades"]
c = json.load(open('local.settings.json'))['Values']['AzureWebJobsStorage']
bs = BlobServiceClient.from_connection_string(c)
total = con_gemini = 0
for cont in CONTENEDORES:
    cc = bs.get_container_client(cont)
    for b in cc.list_blobs():
        try:
            reg = json.loads(cc.get_blob_client(b.name).download_blob().readall())
        except: continue
        total += 1
        if reg.get("clasificado_por") == "gemini": con_gemini += 1
print(f"Total: {total} | Con Gemini: {con_gemini} | Con reglas: {total-con_gemini}")