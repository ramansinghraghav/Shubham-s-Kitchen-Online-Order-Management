import json
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings


UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"


def fetch_unsplash_image(query: str, access_key: str | None = None) -> str | None:
    api_key = access_key or settings.UNSPLASH_ACCESS_KEY
    if not api_key:
        raise RuntimeError("UNSPLASH_ACCESS_KEY is missing. Add it to your .env file.")

    params = urlencode(
        {
            "query": f"{query} food",
            "client_id": api_key,
            "per_page": 1,
            "orientation": "landscape",
        }
    )

    with urlopen(f"{UNSPLASH_SEARCH_URL}?{params}", timeout=15) as response:
        data = json.load(response)

    results = data.get("results", [])
    if not results:
        return None

    return results[0]["urls"]["small"]
