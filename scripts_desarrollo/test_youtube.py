import os
import json

# Cargar variables de entorno desde local.settings.json
with open("local.settings.json") as f:
    settings = json.load(f)
for k, v in settings["Values"].items():
    os.environ[k] = v

import sys
sys.path.insert(0, ".")
from shared.youtube_client import YouTubeClient

yt     = YouTubeClient()
videos = yt.get_recent_videos("@Accenture", max_results=2)

print(f"Videos obtenidos: {len(videos)}")
for v in videos:
    print(f"  - {v['snippet']['title']}")