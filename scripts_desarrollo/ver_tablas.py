from azure.data.tables import TableServiceClient
import json
c = json.load(open('local.settings.json'))['Values']['AzureWebJobsStorage']
ts = TableServiceClient.from_connection_string(c)

for tabla in ['controlSilver', 'executionLogsSilver']:
    print(f"\n=== {tabla} ===")
    tc = ts.get_table_client(tabla)
    filas = list(tc.list_entities())
    print(f"Total filas: {len(filas)}")
    if filas:
        print("Ejemplo de estructura (primera fila):")
        for k, v in filas[0].items():
            print(f"  {k}: {str(v)[:60]}")