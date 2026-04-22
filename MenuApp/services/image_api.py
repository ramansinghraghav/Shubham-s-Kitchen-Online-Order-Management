import json
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings


UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"


def fetch_unsplash_image(query: str, access_key: str | None = None) -> str | None:
    api_key = access_key or settings.UNSPLASH_ACCESS_KEY
    if not api_key:
        return None

    params = urlencode(
        {
            "query": f"{query} food",
            "client_id": api_key,
            "per_page": 1,
            "orientation": "landscape",
        }
    )

    try:
        with urlopen(f"{UNSPLASH_SEARCH_URL}?{params}", timeout=15) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None

    results = data.get("results", [])
    if not results:
        return None

    return results[0]["urls"]["small"]
