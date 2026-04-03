from urllib.error import HTTPError, URLError

from django.core.management.base import BaseCommand

from MenuApp.models import MenuItem
from MenuApp.services.image_api import fetch_unsplash_image


class Command(BaseCommand):
    help = "Fetch menu item image URLs from Unsplash and save them to the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Replace existing image URLs too.",
        )

    def handle(self, *args, **options):
        updated = 0
        skipped = 0
        failed = 0

        for item in MenuItem.objects.all():
            if item.image and item.image.startswith(("http://", "https://")) and not options["force"]:
                skipped += 1
                self.stdout.write(f"Skipping {item.name}: already using API image")
                continue

            try:
                image_url = fetch_unsplash_image(item.name)
            except HTTPError as exc:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f"Failed for {item.name}: API returned HTTP {exc.code}")
                )
                continue
            except URLError as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"Failed for {item.name}: {exc.reason}"))
                continue

            if not image_url:
                self.stdout.write(self.style.WARNING(f"No image found for {item.name}"))
                continue

            item.image = image_url
            item.save(update_fields=["image"])
            updated += 1
            self.stdout.write(self.style.SUCCESS(f"Updated {item.name}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Updated {updated} item(s), skipped {skipped} item(s), failed {failed} item(s)."
            )
        )
