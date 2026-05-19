import os
import json

with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

import sys
sys.path.insert(0, ".")
from shared.apify_client import ApifyClient, build_instagram_payload, ACTOR_INSTAGRAM

apify = ApifyClient()
items = apify.run_actor(ACTOR_INSTAGRAM, build_instagram_payload([
    "https://www.instagram.com/accenture/"
]))

print(f"Items obtenidos: {len(items)}")
for item in items[:2]:
    print(f"  - @{item.get('ownerUsername')} | {str(item.get('caption', ''))[:80]}")