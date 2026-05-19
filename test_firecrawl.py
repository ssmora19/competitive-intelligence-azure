import os
import json

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

import sys
sys.path.insert(0, ".")
from shared.firecrawl_client import FirecrawlClient

fc        = FirecrawlClient()
resultado = fc.scrape("https://www.accenture.com/co-es")

if resultado:
    markdown = resultado.get("markdown", "")
    print(f"Conexión exitosa")
    print(f"Chars obtenidos: {len(markdown)}")
    print(f"Primeros 300 chars:\n{markdown[:300]}")
else:
    print("Sin resultado — revisa la API key")