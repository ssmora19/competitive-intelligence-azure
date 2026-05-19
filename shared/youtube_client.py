"""
shared/youtube_client.py
=========================
Cliente para YouTube Data API v3.

Centraliza:
  - resolución de handle/@canal → uploads playlist
  - obtención de video IDs desde la playlist
  - obtención de detalles completos (snippet, statistics, contentDetails)

La function de competidores solo llama get_recent_videos(handle_or_id).
"""

import os
import logging
from typing import Optional

import requests

log = logging.getLogger("ci.youtube")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
BASE_URL        = "https://www.googleapis.com/youtube/v3"


class YouTubeClient:
    """
    Cliente reutilizable para YouTube Data API v3.

    Uso:
        client = YouTubeClient()
        videos = client.get_recent_videos("@Accenture", max_results=5)
    """

    def __init__(self, api_key: str = None):
        self._key = api_key or YOUTUBE_API_KEY

    def get_uploads_playlist(self, handle_or_id: str) -> Optional[str]:
        """
        Resuelve un handle (@Accenture) o channel ID (UCxxx) al playlist
        de uploads del canal.

        Retorna el playlist ID, o None si el canal no existe.
        """
        params = {"part": "contentDetails", "key": self._key}

        if handle_or_id.startswith("UC"):
            params["id"] = handle_or_id
        else:
            handle = (
                handle_or_id if handle_or_id.startswith("@")
                else f"@{handle_or_id}"
            )
            params["forHandle"] = handle

        try:
            resp = requests.get(f"{BASE_URL}/channels", params=params, timeout=30)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            if not items:
                log.warning("YouTube: canal no encontrado — %s", handle_or_id)
                return None
            return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        except requests.HTTPError as ex:
            log.error("YouTube channels API error [%s]: %s", handle_or_id, ex)
            return None
        except Exception as ex:
            log.error("YouTube channels error [%s]: %s", handle_or_id, ex)
            return None

    def get_video_ids(self, playlist_id: str, max_results: int = 5) -> list[str]:
        """
        Obtiene los IDs de los videos más recientes de una playlist.
        """
        try:
            resp = requests.get(
                f"{BASE_URL}/playlistItems",
                params={
                    "part":       "snippet",
                    "playlistId": playlist_id,
                    "maxResults": max_results,
                    "key":        self._key,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return [
                item["snippet"]["resourceId"]["videoId"]
                for item in resp.json().get("items", [])
            ]
        except Exception as ex:
            log.error("YouTube playlistItems error [%s]: %s", playlist_id, ex)
            return []

    def get_video_details(self, video_ids: list[str]) -> list[dict]:
        """
        Obtiene detalles completos (snippet + statistics + contentDetails)
        de una lista de video IDs.

        Retorna los items crudos de la API, sin modificar.
        """
        if not video_ids:
            return []
        try:
            resp = requests.get(
                f"{BASE_URL}/videos",
                params={
                    "part": "snippet,statistics,contentDetails",
                    "id":   ",".join(video_ids),
                    "key":  self._key,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
        except Exception as ex:
            log.error("YouTube videos error: %s", ex)
            return []

    def get_recent_videos(
        self,
        handle_or_id: str,
        max_results:  int = 5,
    ) -> list[dict]:
        """
        Método principal: dado un handle o channel ID, retorna los
        N videos más recientes con todos sus detalles crudos.

        Retorna [] si el canal no existe o hay error.
        """
        playlist_id = self.get_uploads_playlist(handle_or_id)
        if not playlist_id:
            return []

        video_ids = self.get_video_ids(playlist_id, max_results)
        if not video_ids:
            return []

        return self.get_video_details(video_ids)
