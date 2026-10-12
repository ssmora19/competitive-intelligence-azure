import os
import json

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

import sys
sys.path.insert(0, ".")
from shared.apify_client import ApifyClient, build_tiktok_payload, ACTOR_TIKTOK

apify  = ApifyClient()
items  = apify.run_actor(ACTOR_TIKTOK, build_tiktok_payload(["ibm"]))

print(f"Items obtenidos: {len(items)}")
for item in items[:2]:
    meta = item.get("authorMeta") or {}
    print(f"  - {meta.get('name', 'sin nombre')} | {item.get('text', '')[:80]}")