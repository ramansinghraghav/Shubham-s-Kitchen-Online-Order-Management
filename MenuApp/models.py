from django.db import models
from datetime import timedelta
import re

from django.contrib.auth.models import User


class MenuItem(models.Model):

    name = models.CharField(max_length=100)
    category = models.CharField(max_length=50)
    price = models.CharField(max_length=50)
    description = models.TextField()
    image = models.URLField(blank=True, null=True)  # Store image path or URL

    def __str__(self) -> str:
        return self.name

    @property
    def image_url(self):
        from django.conf import settings
        from django.templatetags.static import static
        from pathlib import Path

        if self.image and self.image.startswith(('http://', 'https://')):
            return self.image

        relative_path = self.image or ''
        media_path = Path(settings.MEDIA_ROOT) / relative_path

        if media_path.exists():
            return f"{settings.MEDIA_URL}{relative_path}"

        return static(relative_path)


class Profile(models.Model):
    user = models.OneToOneField(User, related_name='profile', on_delete=models.CASCADE)
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    address = models.TextField()
    profile_photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True)

    def __str__(self) -> str:
        return self.user.username

    @property
    def photo_url(self):
        if self.profile_photo:
            return self.profile_photo.url

        from urllib.parse import quote
        name = quote(self.user.username or 'User')
        return f"https://ui-avatars.com/api/?name={name}&background=ffb700&color=000000&size=128"


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("preparing", "Preparation started"),
        ("ready", "Ready for pickup"),
        ("completed", "Delivered"),
        ("cancelled", "Cancelled"),
    ]

    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    receiver_name = models.CharField(max_length=120)
    receiver_phone = models.CharField(max_length=30)
    receiver_address = models.TextField()
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2)
    grand_total = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    cook_prep_time = models.CharField(max_length=50, blank=True, null=True)
    cook_prep_minutes = models.PositiveIntegerField(blank=True, null=True)
    cook_extra_minutes = models.PositiveIntegerField(default=0)
    cook_prep_started_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order #{self.pk} - {self.receiver_name}" 

    @property
    def item_count(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total_prep_minutes(self):
        if self.cook_prep_minutes is None:
            return None
        return self.cook_prep_minutes + self.cook_extra_minutes

    @property
    def cook_prep_label(self):
        total_minutes = self.total_prep_minutes
        if total_minutes is None:
            return self.cook_prep_time or ""

        if self.cook_extra_minutes:
            return f"{total_minutes} min ({self.cook_prep_minutes} + {self.cook_extra_minutes})"
        return f"{total_minutes} min"

    @property
    def cook_prep_end(self):
        if not self.cook_prep_started_at:
            return None

        total_minutes = self.total_prep_minutes
        if total_minutes is None and self.cook_prep_time:
            match = re.search(r"(\d+)", self.cook_prep_time)
            if match:
                total_minutes = int(match.group(1))

        if total_minutes is None:
            return None

        return self.cook_prep_started_at + timedelta(minutes=total_minutes)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.menu_item.name} x {self.quantity}"

