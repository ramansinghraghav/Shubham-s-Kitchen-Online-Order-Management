from django.db import models
from django.core.exceptions import ValidationError
from datetime import timedelta
import re
from decimal import Decimal

from django.contrib.auth.models import User


class MenuItem(models.Model):
    PORTION_CHOICES = [
        ("half", "Half"),
        ("full", "Full"),
    ]
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=50)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    half_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    full_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    description = models.TextField()
    image = models.URLField(max_length=500, blank=True, null=True)  # Store image path or URL

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

    @property
    def supports_portions(self):
        return self.half_price is not None

    @property
    def display_price(self):
        return self.full_price if self.full_price is not None else self.price

    @property
    def average_rating(self):
        ratings = [item.rating for item in self.order_items.exclude(rating__isnull=True)]
        if not ratings:
            return None
        return round(sum(ratings) / len(ratings), 1)

    @property
    def rating_count(self):
        return self.order_items.exclude(rating__isnull=True).count()

    def get_price_for_portion(self, portion):
        if self.supports_portions:
            if portion == "half":
                return self.half_price
            return self.full_price if self.full_price is not None else self.price
        return self.full_price if self.full_price is not None else self.price

    def clean(self):
        errors = {}
        effective_full_price = self.full_price if self.full_price is not None else self.price
        if effective_full_price is None:
            errors["full_price"] = "Full price is required."
        if self.half_price is not None and effective_full_price is not None and self.half_price >= effective_full_price:
            errors["half_price"] = "Half price must be lower than full price."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.full_price is None:
            self.full_price = self.price
        self.price = self.full_price
        self.clean()
        return super().save(*args, **kwargs)


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
    PAYMENT_METHOD_CHOICES = [
        ("cod", "Cash on delivery"),
        ("razorpay", "Pay now"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("preparing", "Preparation started"),
        ("ready", "Ready for pickup"),
        ("completed", "Delivered"),
        ("cancelled", "Cancelled"),
    ]
    PAYMENT_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
    ]
    ALLOWED_STATUS_TRANSITIONS = {
        "pending": {"accepted", "cancelled"},
        "accepted": {"preparing", "cancelled"},
        "preparing": {"ready", "cancelled"},
        "ready": {"completed", "cancelled"},
        "completed": set(),
        "cancelled": set(),
    }

    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    receiver_name = models.CharField(max_length=120)
    receiver_phone = models.CharField(max_length=30)
    receiver_address = models.TextField()
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2)
    grand_total = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default="cod")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default="pending")
    payment_provider = models.CharField(max_length=30, blank=True)
    payment_reference = models.CharField(max_length=100, blank=True, db_index=True)
    payment_due_at = models.DateTimeField(blank=True, null=True)
    confirmed_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True)
    cook_prep_time = models.CharField(max_length=50, blank=True, null=True)
    cook_prep_minutes = models.PositiveIntegerField(blank=True, null=True)
    cook_extra_minutes = models.PositiveIntegerField(default=0)
    cook_prep_started_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order #{self.pk} - {self.receiver_name}" 

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["payment_status", "created_at"]),
        ]

    @property
    def item_count(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def is_confirmed(self):
        return self.confirmed_at is not None

    @property
    def requires_online_payment(self):
        return self.payment_method == "razorpay"

    @property
    def is_payment_expired(self):
        return self.requires_online_payment and self.payment_status != "paid" and self.payment_due_at is not None

    @property
    def cook_timeline_end(self):
        if not self.confirmed_at or self.status in {"completed", "cancelled"}:
            return None
        return self.confirmed_at + timedelta(minutes=30)

    def confirm_order(self, *, save=True, timestamp=None):
        from django.utils import timezone

        confirmed_time = timestamp or timezone.now()
        self.confirmed_at = confirmed_time
        if self.payment_method == "cod":
            self.payment_status = "pending"
        elif self.payment_status != "paid":
            self.payment_status = "paid"
        if save:
            self.save(update_fields=["confirmed_at", "payment_status", "updated_at"])

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

    def clean(self):
        errors = {}

        if not self.receiver_name.strip():
            errors["receiver_name"] = "Receiver name is required."

        if len(re.sub(r"\D", "", self.receiver_phone or "")) < 10:
            errors["receiver_phone"] = "Enter a valid phone number."

        if not self.receiver_address.strip():
            errors["receiver_address"] = "Receiver address is required."

        if self.subtotal < 0 or self.delivery_fee < 0 or self.tax_amount < 0 or self.grand_total < 0:
            errors["grand_total"] = "Order totals cannot be negative."

        expected_total = self.subtotal + self.delivery_fee + self.tax_amount
        if self.grand_total != expected_total:
            errors["grand_total"] = "Grand total must equal subtotal + delivery fee + tax amount."

        if self.cook_extra_minutes and self.cook_prep_minutes is None:
            errors["cook_extra_minutes"] = "Base prep time is required before adding extra minutes."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.receiver_name = self.receiver_name.strip()
        self.receiver_phone = self.receiver_phone.strip()
        self.receiver_address = self.receiver_address.strip()
        self.notes = self.notes.strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def can_transition_to(self, new_status):
        return new_status in self.ALLOWED_STATUS_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status, *, save=True):
        if new_status == self.status:
            return
        if not self.can_transition_to(new_status):
            raise ValidationError(f"Cannot move order from {self.status} to {new_status}.")
        self.status = new_status
        if save:
            self.save(update_fields=["status", "updated_at"])

    def mark_payment(self, provider, reference, *, save=True):
        self.payment_provider = provider
        self.payment_reference = reference
        self.payment_status = "paid"
        self.payment_method = "razorpay"
        if self.confirmed_at is None:
            self.confirm_order(save=False)
        if save:
            self.save(
                update_fields=[
                    "payment_provider",
                    "payment_reference",
                    "payment_status",
                    "payment_method",
                    "confirmed_at",
                    "updated_at",
                ]
            )

    def mark_payment_failed(self, provider, reference="", *, save=True):
        self.payment_provider = provider
        self.payment_reference = reference
        self.payment_status = "failed"
        if save:
            self.save(update_fields=["payment_provider", "payment_reference", "payment_status", "updated_at"])


class OrderItem(models.Model):
    PORTION_CHOICES = [
        ("full", "Full"),
        ("half", "Half"),
    ]
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT, related_name="order_items")
    quantity = models.PositiveIntegerField(default=1)
    portion = models.CharField(max_length=10, choices=PORTION_CHOICES, default="full")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=10, decimal_places=2)
    rating = models.PositiveSmallIntegerField(blank=True, null=True)

    def __str__(self):
        return f"{self.menu_item.name} x {self.quantity}"

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="order_item_quantity_gt_zero"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="order_item_unit_price_gte_zero"),
            models.CheckConstraint(condition=models.Q(line_total__gte=0), name="order_item_line_total_gte_zero"),
        ]

    def clean(self):
        errors = {}
        if self.quantity <= 0:
            errors["quantity"] = "Quantity must be greater than zero."
        if self.rating is not None and self.rating not in {1, 2, 3, 4, 5}:
            errors["rating"] = "Rating must be between 1 and 5 stars."
        expected_total = self.unit_price * self.quantity
        if self.line_total != expected_total:
            errors["line_total"] = "Line total must equal unit price multiplied by quantity."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PaymentEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payment_events")
    provider = models.CharField(max_length=30)
    provider_event_id = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=100)
    payment_id = models.CharField(max_length=100, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default="processed")

    class Meta:
        ordering = ["-processed_at"]
        indexes = [
            models.Index(fields=["provider", "event_type"]),
            models.Index(fields=["order", "processed_at"]),
        ]

    def __str__(self):
        return f"{self.provider}:{self.provider_event_id}"

