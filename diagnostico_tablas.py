from azure.data.tables import TableServiceClient
import json
from collections import Counter
c = json.load(open('local.settings.json'))['Values']['AzureWebJobsStorage']
ts = TableServiceClient.from_connection_string(c)
tc = ts.get_table_client('executionLogsSilver')
filas = list(tc.list_entities())
print("Contenedores registrados:", Counter(f['contenedor'] for f in filas))
print("Total escritos:", sum(int(f.get('escritos',0)) for f in filas))
print("Total duplicados:", sum(int(f.get('skip_duplicado',0)) for f in filas))