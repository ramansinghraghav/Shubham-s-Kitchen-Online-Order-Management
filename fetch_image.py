import os
from pathlib import Path

import django
import requests


def load_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env_file()

# Django setup
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Skproject.settings")
django.setup()

from MenuApp.models import MenuItem

ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")

if not ACCESS_KEY:
    raise RuntimeError("UNSPLASH_ACCESS_KEY is missing. Add it to your .env file.")


def fetch_unsplash_image(query: str) -> str | None:
    response = requests.get(
        "https://api.unsplash.com/search/photos",
        params={
            "query": f"{query} food",
            "client_id": ACCESS_KEY,
            "per_page": 1,
            "orientation": "landscape",
        },
        timeout=15,
    )
    response.raise_for_status()

    data = response.json()
    results = data.get("results", [])
    if not results:
        return None

    return results[0]["urls"]["small"]

for item in MenuItem.objects.all():
    if item.image and item.image.startswith(("http://", "https://")):
        print(f"Skipping {item.name}: already using API image")
        continue

    image_url = fetch_unsplash_image(item.name)
    if not image_url:
        print(f"No image found for {item.name}")
        continue

    item.image = image_url
    item.save(update_fields=["image"])
    print(f"{item.name} updated")
print("Done ✅")
