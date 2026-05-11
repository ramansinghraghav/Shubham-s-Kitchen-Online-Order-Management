import json
import re
from decimal import Decimal, InvalidOperation
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.core.paginator import EmptyPage, Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, HttpResponse
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from .forms import SignUpForm, ProfileForm
from .jwt_auth import JWTValidationError, build_token_pair, get_user_from_token
from .models import MenuItem, PaymentEvent, Profile, Order, OrderItem
from .services.payments import (
    RazorpayAPIError,
    RazorpayConfigurationError,
    create_razorpay_order,
    verify_razorpay_webhook_signature,
)


DELIVERY_FEE = Decimal("35.00")
TAX_RATE = Decimal("0.05")
ORDER_PAGE_HISTORY_HOURS = 2
PAYMENT_WINDOW_MINUTES = 5
AUTO_ACCEPT_MINUTES = 5
AUTO_PREPARING_MINUTES = 15
AUTO_READY_MINUTES = 10
INITIAL_PREP_MINUTE_OPTIONS = (15, 20, 25)
EXTRA_PREP_MINUTE_OPTIONS = (5, 10)
COOK_HISTORY_FILTERS = ("week", "month", "year")
RESTAURANT_PHONE_DISPLAY = "+91 9079172810"
RESTAURANT_PHONE_LINK = "+919079172810"
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50
AUTH_RATE_LIMIT_COUNT = 10
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60
PAYMENT_RATE_LIMIT_COUNT = 20
PAYMENT_RATE_LIMIT_WINDOW_SECONDS = 60


def parse_price(price) -> Decimal:
    if isinstance(price, Decimal):
        return price.quantize(Decimal("0.01"))

    cleaned_price = re.sub(r"[^\d.]", "", str(price or ""))
    if not cleaned_price:
        return Decimal("0.00")

    try:
        return Decimal(cleaned_price).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def get_json_payload(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationError("Invalid JSON payload.")


def json_error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def client_rate_limit_key(request, key_prefix):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else request.META.get("REMOTE_ADDR", "unknown")
    return f"rate:{key_prefix}:{client_ip}"


def rate_limit(*, key_prefix, limit, window_seconds):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            rate_key = client_rate_limit_key(request, key_prefix)
            current_count = cache.get(rate_key, 0)
            if current_count >= limit:
                return json_error("Too many requests. Please try again shortly.", status=429)
            if current_count == 0:
                cache.set(rate_key, 1, timeout=window_seconds)
            else:
                cache.incr(rate_key)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def error_page(request, status_code, title, message):
    response = render(
        request,
        "error.html",
        {
            "status_code": status_code,
            "error_title": title,
            "error_message": message,
        },
        status=status_code,
    )
    return response


def get_bearer_token(request):
    authorization = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise JWTValidationError("Authorization header must use Bearer token.")
    return authorization[len(prefix):].strip()


def get_authenticated_user_from_bearer(request, *, expected_type="access"):
    token = get_bearer_token(request)
    return get_user_from_token(token, expected_type=expected_type)


def get_page_size(request, default=DEFAULT_PAGE_SIZE):
    try:
        page_size = int(request.GET.get("page_size", default))
    except (TypeError, ValueError):
        page_size = default
    return max(1, min(page_size, MAX_PAGE_SIZE))


def paginate_queryset(request, queryset, default=DEFAULT_PAGE_SIZE):
    paginator = Paginator(queryset, get_page_size(request, default))
    page_number = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page_number)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)
    return page_obj


def serialize_menu_item(item):
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "price": str(item.display_price),
        "half_price": str(item.half_price) if item.half_price is not None else None,
        "full_price": str(item.full_price) if item.full_price is not None else None,
        "supports_portions": item.supports_portions,
        "average_rating": item.average_rating,
        "rating_count": item.rating_count,
        "description": item.description,
        "image_url": item.image_url,
    }


def serialize_order_item(item):
    return {
        "id": item.id,
        "menu_item_id": item.menu_item_id,
        "name": item.menu_item.name,
        "portion": item.portion,
        "portion_label": item.get_portion_display(),
        "quantity": item.quantity,
        "unit_price": str(item.unit_price),
        "line_total": str(item.line_total),
        "rating": item.rating,
    }


def serialize_order(order):
    return {
        "id": order.id,
        "receiver_name": order.receiver_name,
        "receiver_phone": order.receiver_phone,
        "receiver_address": order.receiver_address,
        "subtotal": str(order.subtotal),
        "delivery_fee": str(order.delivery_fee),
        "tax_amount": str(order.tax_amount),
        "grand_total": str(order.grand_total),
        "status": order.status,
        "status_label": order.get_status_display(),
        "payment_method": order.payment_method,
        "payment_method_label": order.get_payment_method_display(),
        "payment_status": order.payment_status,
        "payment_status_label": order.get_payment_status_display(),
        "payment_provider": order.payment_provider,
        "payment_reference": order.payment_reference,
        "payment_due_at": order.payment_due_at.isoformat() if order.payment_due_at else None,
        "confirmed_at": order.confirmed_at.isoformat() if order.confirmed_at else None,
        "is_confirmed": order.is_confirmed,
        "item_count": order.item_count,
        "cook_prep_label": order.cook_prep_label,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
        "items": [serialize_order_item(item) for item in order.items.all()],
    }


def create_and_attach_razorpay_order_for_order(*, order, user):
    if order.user_id != user.id:
        raise Http404("Order not found.")

    if order.payment_method != "razorpay":
        raise ValidationError("This order is not set for online payment.")

    if order.payment_status == "paid":
        raise ValidationError("Order is already paid.")

    sync_order_payment_state(order)
    if order.payment_status == "failed":
        raise ValidationError("Payment window expired. Choose cash on delivery or cancel the order.")

    razorpay_order = create_razorpay_order(
        amount_rupees=order.grand_total,
        receipt=f"order-{order.id}",
        notes={"order_id": str(order.id), "username": user.username},
    )
    order.payment_provider = "razorpay"
    order.payment_reference = razorpay_order.get("id", "")
    order.save(update_fields=["payment_provider", "payment_reference", "updated_at"])
    return razorpay_order


def build_paginated_response(page_obj, serializer):
    return {
        "count": page_obj.paginator.count,
        "page": page_obj.number,
        "pages": page_obj.paginator.num_pages,
        "page_size": page_obj.paginator.per_page,
        "has_next": page_obj.has_next(),
        "has_previous": page_obj.has_previous(),
        "results": [serializer(obj) for obj in page_obj.object_list],
    }


def validate_receiver_details(receiver_name, receiver_phone, receiver_address):
    errors = {}

    if not receiver_name:
        errors["receiver_name"] = "Receiver name is required."

    if not receiver_phone:
        errors["receiver_phone"] = "Phone number is required."
    elif len(re.sub(r"\D", "", receiver_phone)) < 10:
        errors["receiver_phone"] = "Enter a valid phone number."

    if not receiver_address:
        errors["receiver_address"] = "Address is required."

    return errors


def get_order_timeline(order):
    if not order.confirmed_at:
        return None

    confirmed_at = order.confirmed_at
    accept_deadline = confirmed_at + timedelta(minutes=AUTO_ACCEPT_MINUTES)
    preparing_deadline = accept_deadline + timedelta(minutes=AUTO_PREPARING_MINUTES)
    ready_deadline = preparing_deadline + timedelta(minutes=AUTO_READY_MINUTES)
    return {
        "confirmed_at": confirmed_at,
        "accept_deadline": accept_deadline,
        "preparing_deadline": preparing_deadline,
        "ready_deadline": ready_deadline,
    }


def sync_order_payment_state(order, now=None):
    current_time = now or timezone.now()
    if (
        order.requires_online_payment
        and not order.is_confirmed
        and order.payment_status == "pending"
        and order.payment_due_at is not None
        and current_time >= order.payment_due_at
    ):
        order.payment_status = "failed"
        order.save(update_fields=["payment_status", "updated_at"])
    return order


def sync_order_workflow(order, now=None):
    current_time = now or timezone.now()
    sync_order_payment_state(order, now=current_time)

    if not order.is_confirmed or order.status in {"completed", "cancelled"}:
        return order

    timeline = get_order_timeline(order)
    if timeline is None:
        return order

    changed_fields = []

    if current_time >= timeline["ready_deadline"]:
        if order.status != "completed":
            order.status = "completed"
            changed_fields.extend(["status", "updated_at"])
    elif current_time >= timeline["preparing_deadline"]:
        if order.status != "ready":
            order.status = "ready"
            changed_fields.extend(["status", "updated_at"])
    elif current_time >= timeline["accept_deadline"]:
        if order.status != "preparing":
            order.status = "preparing"
            changed_fields.extend(["status", "updated_at"])
        if order.cook_prep_minutes != AUTO_PREPARING_MINUTES:
            order.cook_prep_minutes = AUTO_PREPARING_MINUTES
            changed_fields.append("cook_prep_minutes")
        if order.cook_extra_minutes != 0:
            order.cook_extra_minutes = 0
            changed_fields.append("cook_extra_minutes")
        if order.cook_prep_time != f"{AUTO_PREPARING_MINUTES} min":
            order.cook_prep_time = f"{AUTO_PREPARING_MINUTES} min"
            changed_fields.append("cook_prep_time")
        if order.cook_prep_started_at != timeline["accept_deadline"]:
            order.cook_prep_started_at = timeline["accept_deadline"]
            changed_fields.append("cook_prep_started_at")

    if changed_fields:
        order.save(update_fields=list(dict.fromkeys(changed_fields)))

    return order


def build_order_page_context(order):
    timeline = get_order_timeline(order) if order else None
    return {
        "last_order": order,
        "order_page_history_hours": ORDER_PAGE_HISTORY_HOURS,
        "restaurant_phone_display": RESTAURANT_PHONE_DISPLAY,
        "restaurant_phone_link": RESTAURANT_PHONE_LINK,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "payment_window_minutes": PAYMENT_WINDOW_MINUTES,
        "order_timeline": timeline,
        "auto_accept_minutes": AUTO_ACCEPT_MINUTES,
        "auto_preparing_minutes": AUTO_PREPARING_MINUTES,
        "auto_ready_minutes": AUTO_READY_MINUTES,
    }


def make_cart_key(item_id, portion="full"):
    return f"{item_id}:{portion or 'full'}"


def parse_cart_key(cart_key):
    raw_key = str(cart_key or "")
    if ":" in raw_key:
        item_id, portion = raw_key.split(":", 1)
        return item_id, portion or "full"
    return raw_key, "full"


def get_item_cart_quantity(cart, item_id, portion="full"):
    base_key = str(item_id)
    portion_key = make_cart_key(item_id, portion)
    if portion == "full":
        return cart.get(portion_key, 0) + cart.get(base_key, 0)
    return cart.get(portion_key, 0)


def build_cart_items(cart):
    item_ids = []
    for cart_key in cart.keys():
        item_id, _ = parse_cart_key(cart_key)
        item_ids.append(int(item_id))
    menu_items = MenuItem.objects.in_bulk(item_ids)
    cart_items = []
    subtotal = Decimal("0.00")

    for cart_key, quantity in cart.items():
        item_id, portion = parse_cart_key(cart_key)
        menu_item = menu_items.get(int(item_id))
        if not menu_item:
            continue

        unit_price = parse_price(menu_item.get_price_for_portion(portion))
        line_total = unit_price * quantity
        subtotal += line_total
        cart_items.append(
            {
                "item": menu_item,
                "cart_key": cart_key,
                "portion": portion,
                "portion_label": "Half" if portion == "half" else "Full",
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )

    tax_amount = (subtotal * TAX_RATE).quantize(Decimal("0.01"))
    grand_total = subtotal + DELIVERY_FEE + tax_amount if cart_items else Decimal("0.00")

    return cart_items, subtotal, tax_amount, grand_total


def build_order_snapshot(cart_items, subtotal, tax_amount, grand_total):
    return {
        "items": [
            {
                "name": cart_item["item"].name,
                "category": cart_item["item"].category,
                "quantity": cart_item["quantity"],
                "unit_price": str(cart_item["unit_price"]),
                "line_total": str(cart_item["line_total"]),
            }
            for cart_item in cart_items
        ],
        "item_count": sum(cart_item["quantity"] for cart_item in cart_items),
        "subtotal": str(subtotal),
        "delivery_fee": str(DELIVERY_FEE if cart_items else Decimal("0.00")),
        "tax_amount": str(tax_amount),
        "grand_total": str(grand_total),
    }


def is_order_visible_on_order_page(order):
    if order is None:
        return False

    if order.status not in {"completed", "cancelled"}:
        return True

    cutoff = timezone.now() - timedelta(hours=ORDER_PAGE_HISTORY_HOURS)
    return order.updated_at >= cutoff


def get_visible_order_for_request(request, order_id=None, order_page_only=False):
    if request.user.is_authenticated:
        queryset = Order.objects.filter(user=request.user).prefetch_related("items__menu_item")
        if order_id is not None:
            return get_object_or_404(queryset, pk=order_id)
        orders = queryset.order_by("-created_at")
        if not order_page_only:
            return orders.first()

        for order in orders:
            if is_order_visible_on_order_page(order):
                return order
        return None

    session_order_id = request.session.get("last_order_id")
    if session_order_id is None:
        return None

    if order_id is not None and int(order_id) != int(session_order_id):
        raise Http404("Order not found")

    order = (
        Order.objects.filter(pk=session_order_id)
        .prefetch_related("items__menu_item")
        .first()
    )
    if order_page_only and not is_order_visible_on_order_page(order):
        return None
    return order


def parse_minutes(value):
    if value in (None, ""):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_cook_history_orders(filter_by):
    today = timezone.localdate()
    now = timezone.now()
    queryset = Order.objects.exclude(created_at__date=today).order_by("-created_at")

    if filter_by == "week":
        return queryset.filter(created_at__gte=now - timedelta(days=7))
    if filter_by == "month":
        return queryset.filter(created_at__gte=now - timedelta(days=30))
    if filter_by == "year":
        return queryset.filter(created_at__gte=now - timedelta(days=365))
    return queryset


def home_view(request):
    if not request.user.is_authenticated:
        return render(request, "welcome.html")

    if request.user.is_staff:
        return redirect("cook_orders_view")

    featured_items = []
    seen_categories = set()

    for item in MenuItem.objects.exclude(image__isnull=True).exclude(image__exact=""):
        if item.category in seen_categories:
            continue

        featured_items.append(item)
        seen_categories.add(item.category)

        if len(featured_items) == 7:
            break

    return render(request, 'index.html', {'featured_items': featured_items})

def contact_view(request):
    return render(request,'contact.html')

def services_view(request):
    return render(request,'services.html')

def menu_view(request):
    items = MenuItem.objects.all()
    cart_added = request.GET.get("cart_added") == "1"
    cart = request.session.get("cart", {})

    grouped_menu = {}
    for item in items:
        item.cart_quantities = {
            "half": get_item_cart_quantity(cart, item.id, "half"),
            "full": get_item_cart_quantity(cart, item.id, "full"),
        }
        item.cart_quantity = sum(item.cart_quantities.values())
        grouped_menu.setdefault(item.category, []).append(item)

    return render(request, 'menu.html', {
        'grouped_menu': grouped_menu,
        'cart_added': cart_added,
    })


@require_GET
def menu_api_view(request):
    queryset = MenuItem.objects.all().order_by("category", "name")
    category = request.GET.get("category", "").strip()
    search = request.GET.get("search", "").strip()

    if category:
        queryset = queryset.filter(category__iexact=category)
    if search:
        queryset = queryset.filter(name__icontains=search)

    page_obj = paginate_queryset(request, queryset, default=12)
    return JsonResponse({"ok": True, **build_paginated_response(page_obj, serialize_menu_item)})


@require_GET
def menu_detail_api_view(request, item_id):
    item = get_object_or_404(MenuItem, pk=item_id)
    return JsonResponse({"ok": True, "item": serialize_menu_item(item)})

def cart_view(request):
    cart = request.session.get("cart", {})

    if request.method == "POST":
        action = request.POST.get("action")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        def cart_json_response(item=None):
            portion = request.POST.get("item_portion", "full")
            if item and not item.supports_portions:
                item_key = str(item.pk)
            else:
                item_key = make_cart_key(item.pk, portion) if item else make_cart_key(request.POST.get("item_id", ""), portion)
            return JsonResponse(
                {
                    "ok": True,
                    "item_name": item.name if item else "",
                    "item_portion": portion,
                    "cart_count": sum(cart.values()),
                    "item_count": cart.get(item_key, 0),
                }
            )

        if action == "add":
            item = get_object_or_404(MenuItem, pk=request.POST.get("item_id"))
            portion = request.POST.get("item_portion", "full")
            item_key = make_cart_key(item.pk, portion) if item.supports_portions else str(item.pk)
            cart[item_key] = cart.get(item_key, 0) + 1
            request.session["cart"] = cart
            request.session.modified = True
            if is_ajax:
                return cart_json_response(item)
            next_url = request.POST.get("next_url")
            if next_url == "menu":
                return redirect("/menu/?cart_added=1")
            return redirect("cart_view")

        if action == "increase":
            item_key = request.POST.get("item_key") or make_cart_key(request.POST.get("item_id"), request.POST.get("item_portion", "full"))
            if item_key not in cart and ":" in item_key:
                item_id, portion = parse_cart_key(item_key)
                plain_key = str(item_id)
                if portion == "full" and plain_key in cart:
                    item_key = plain_key
            if item_key in cart:
                cart[item_key] += 1
                request.session["cart"] = cart
                request.session.modified = True
            if is_ajax:
                item_id, _ = parse_cart_key(item_key)
                item = get_object_or_404(MenuItem, pk=item_id)
                return cart_json_response(item)
            return redirect("cart_view")

        if action == "decrease":
            item_key = request.POST.get("item_key") or make_cart_key(request.POST.get("item_id"), request.POST.get("item_portion", "full"))
            if item_key not in cart and ":" in item_key:
                item_id, portion = parse_cart_key(item_key)
                plain_key = str(item_id)
                if portion == "full" and plain_key in cart:
                    item_key = plain_key
            item_id, _ = parse_cart_key(item_key)
            item = get_object_or_404(MenuItem, pk=item_id)
            if item_key in cart:
                cart[item_key] -= 1
                if cart[item_key] <= 0:
                    del cart[item_key]
                request.session["cart"] = cart
                request.session.modified = True
            if is_ajax:
                return cart_json_response(item)
            return redirect("cart_view")

        if action == "remove":
            item_key = request.POST.get("item_key") or make_cart_key(request.POST.get("item_id"), request.POST.get("item_portion", "full"))
            if item_key not in cart and ":" in item_key:
                item_id, portion = parse_cart_key(item_key)
                plain_key = str(item_id)
                if portion == "full" and plain_key in cart:
                    item_key = plain_key
            if item_key in cart:
                del cart[item_key]
                request.session["cart"] = cart
                request.session.modified = True
            return redirect("cart_view")

        if action == "place_order":
            cart_items, subtotal, tax_amount, grand_total = build_cart_items(cart)
            receiver_name = request.POST.get("receiver_name", "").strip()
            receiver_phone = request.POST.get("receiver_phone", "").strip()
            receiver_address = request.POST.get("receiver_address", "").strip()
            payment_method = request.POST.get("payment_method", "cod").strip()
            receiver_errors = validate_receiver_details(
                receiver_name,
                receiver_phone,
                receiver_address,
            )

            if not cart_items:
                messages.error(request, "Your cart is empty.")
                return redirect("cart_view")

            if receiver_errors:
                for error in receiver_errors.values():
                    messages.error(request, error)
                return redirect("cart_view")

            if payment_method not in {"cod", "razorpay"}:
                messages.error(request, "Choose a valid payment option.")
                return redirect("cart_view")

            with transaction.atomic():
                order = Order.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    receiver_name=receiver_name,
                    receiver_phone=receiver_phone,
                    receiver_address=receiver_address,
                    subtotal=subtotal,
                    delivery_fee=DELIVERY_FEE if cart_items else Decimal("0.00"),
                    tax_amount=tax_amount,
                    grand_total=grand_total,
                    payment_method=payment_method,
                    payment_due_at=timezone.now() + timedelta(minutes=PAYMENT_WINDOW_MINUTES) if payment_method == "razorpay" else None,
                    confirmed_at=timezone.now() if payment_method == "cod" else None,
                )

                for cart_item in cart_items:
                    OrderItem.objects.create(
                        order=order,
                        menu_item=cart_item["item"],
                        portion=cart_item["portion"],
                        quantity=cart_item["quantity"],
                        unit_price=cart_item["unit_price"],
                        line_total=cart_item["line_total"],
                    )

            request.session["last_order_id"] = order.id
            request.session["cart"] = {}
            request.session.modified = True
            if payment_method == "cod":
                messages.success(request, "Order placed with cash on delivery.")
            else:
                messages.info(request, f"Order created. Complete online payment within {PAYMENT_WINDOW_MINUTES} minutes.")
            return redirect("order_view")

    if request.GET.get("add"):
        item = get_object_or_404(MenuItem, pk=request.GET.get("add"))
        item_key = make_cart_key(item.pk, request.GET.get("item_portion", "full")) if item.supports_portions else str(item.pk)
        cart[item_key] = cart.get(item_key, 0) + 1
        request.session["cart"] = cart
        request.session.modified = True
        return redirect("cart_view")

    cart_items, subtotal, tax_amount, grand_total = build_cart_items(cart)
    grouped_menu = {}
    for item in MenuItem.objects.all():
        grouped_menu.setdefault(item.category, []).append(item)

    context = {
        "grouped_menu": grouped_menu,
        "cart_items": cart_items,
        "cart_count": sum(item["quantity"] for item in cart_items),
        "subtotal": subtotal,
        "delivery_fee": DELIVERY_FEE if cart_items else Decimal("0.00"),
        "tax_amount": tax_amount,
        "grand_total": grand_total,
    }

    return render(request, 'cart.html', context)


def order_view(request):
    order = get_visible_order_for_request(request, order_page_only=True)
    if order:
        sync_order_workflow(order)
        order.refresh_from_db()
    return render(request, 'order.html', build_order_page_context(order))


def my_orders_view(request):
    if not request.user.is_authenticated:
        return redirect("/profile/?mode=login")

    queryset = (
        Order.objects.filter(user=request.user)
        .prefetch_related("items__menu_item")
        .order_by("-created_at")
    )
    for order in queryset:
        sync_order_workflow(order)
    orders = Order.objects.filter(user=request.user).prefetch_related("items__menu_item").order_by("-created_at")
    page_obj = paginate_queryset(request, orders, default=8)
    return render(request, "my_orders.html", {"orders": page_obj.object_list, "page_obj": page_obj})


def order_detail_view(request, order_id):
    order = get_visible_order_for_request(request, order_id=order_id)
    if order:
        sync_order_workflow(order)
        order.refresh_from_db()
    context = build_order_page_context(order)
    context["is_detail_view"] = True
    return render(request, "order.html", context)


@login_required(login_url="/profile/?mode=login")
def order_payment_action_view(request, order_id):
    if request.method != "POST":
        return redirect("order_detail_view", order_id=order_id)

    order = get_object_or_404(Order, pk=order_id, user=request.user)
    sync_order_workflow(order)
    order.refresh_from_db()
    action = request.POST.get("action")

    if order.is_confirmed:
        messages.info(request, f"Order #{order.id} is already confirmed.")
        return redirect("order_detail_view", order_id=order.id)

    if action == "switch_to_cod":
        order.payment_method = "cod"
        order.payment_status = "pending"
        order.payment_due_at = None
        order.confirm_order(save=False)
        order.save(update_fields=["payment_method", "payment_status", "payment_due_at", "confirmed_at", "updated_at"])
        messages.success(request, f"Order #{order.id} switched to cash on delivery.")
    elif action == "mark_payment_failed":
        if order.payment_method == "razorpay" and order.payment_status == "pending":
            order.mark_payment_failed("razorpay", order.payment_reference)
        messages.info(request, f"Order #{order.id} payment marked failed.")
    elif action == "cancel_order":
        order.status = "cancelled"
        order.save(update_fields=["status", "updated_at"])
        messages.success(request, f"Order #{order.id} cancelled.")
    else:
        messages.error(request, "Unsupported order action.")

    return redirect("order_detail_view", order_id=order.id)


@login_required(login_url="/profile/?mode=login")
def order_item_rating_view(request, order_id, item_id):
    if request.method != "POST":
        return redirect("order_detail_view", order_id=order_id)

    order = get_object_or_404(Order.objects.prefetch_related("items__menu_item"), pk=order_id, user=request.user)
    sync_order_workflow(order)
    order.refresh_from_db()
    if order.status != "completed":
        messages.error(request, "Ratings are available only after delivery.")
        return redirect("order_detail_view", order_id=order_id)

    order_item = get_object_or_404(OrderItem, pk=item_id, order=order)
    rating = parse_minutes(request.POST.get("rating"))
    if rating not in {1, 2, 3, 4, 5}:
        messages.error(request, "Choose a rating between 1 and 5 stars.")
        return redirect("order_detail_view", order_id=order_id)

    order_item.rating = rating
    order_item.save(update_fields=["rating"])
    messages.success(request, f"Rated {order_item.menu_item.name} with {rating} stars.")
    return redirect("order_detail_view", order_id=order_id)


def cook_orders_view(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return HttpResponse("Unauthorized", status=403)

    if request.method == "POST":
        order_id = request.POST.get("order_id")
        action = request.POST.get("action")

        order = Order.objects.filter(pk=order_id).first()
        if order:
            sync_order_workflow(order)
            order.refresh_from_db()
            if action == "accept":
                prep_minutes = parse_minutes(request.POST.get("prep_minutes"))
                if prep_minutes not in INITIAL_PREP_MINUTE_OPTIONS:
                    messages.error(request, "Choose a prep time of 15, 20, or 25 minutes.")
                elif not order.can_transition_to("accepted"):
                    messages.error(request, f"Order #{order.id} cannot be accepted from {order.status}.")
                else:
                    order.status = "accepted"
                    order.cook_prep_minutes = prep_minutes
                    order.cook_extra_minutes = 0
                    order.cook_prep_time = f"{prep_minutes} min"
                    order.cook_prep_started_at = timezone.now()
                    order.save()
                    messages.success(request, f"Order #{order.id} accepted for {prep_minutes} minutes.")

            elif action == "start_preparing":
                try:
                    order.transition_to("preparing")
                    messages.success(request, f"Order #{order.id} preparation started.")
                except ValidationError as exc:
                    messages.error(request, exc.message)

            elif action == "ready":
                try:
                    order.transition_to("ready")
                    messages.success(request, f"Order #{order.id} marked ready for pickup.")
                except ValidationError as exc:
                    messages.error(request, exc.message)

            elif action == "delivered":
                try:
                    order.transition_to("completed")
                    messages.success(request, f"Order #{order.id} marked delivered.")
                except ValidationError as exc:
                    messages.error(request, exc.message)

            elif action == "add_time":
                extra_minutes = parse_minutes(request.POST.get("extra_minutes"))
                if order.cook_prep_minutes is None:
                    messages.error(request, "Accept the order with a prep time before adding more time.")
                elif extra_minutes not in EXTRA_PREP_MINUTE_OPTIONS:
                    messages.error(request, "Choose an extra time of 5 or 10 minutes.")
                else:
                    order.cook_extra_minutes += extra_minutes
                    order.cook_prep_time = f"{order.total_prep_minutes} min"
                    order.save(update_fields=["cook_extra_minutes", "cook_prep_time", "updated_at"])
                    messages.success(request, f"Added {extra_minutes} minutes to Order #{order.id}.")

        return redirect("cook_orders_view")

    today = timezone.localdate()
    sync_queryset = Order.objects.filter(created_at__date=today, confirmed_at__isnull=False)
    for order in sync_queryset:
        sync_order_workflow(order)

    today_orders = (
        Order.objects.filter(created_at__date=today, confirmed_at__isnull=False)
        .exclude(status__in=["completed", "cancelled"])
        .order_by("-created_at")
    )
    today_orders = list(today_orders)
    for order in today_orders:
        order.workflow_timeline = get_order_timeline(order)
    today_count = len(today_orders)
    today_value = sum((order.grand_total for order in today_orders), Decimal("0.00"))

    return render(
        request,
        "cook_orders.html",
        {
            "orders": today_orders,
            "today_count": today_count,
            "today_value": today_value,
            "initial_prep_options": INITIAL_PREP_MINUTE_OPTIONS,
            "extra_prep_options": EXTRA_PREP_MINUTE_OPTIONS,
        },
    )


def cook_order_history_view(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return HttpResponse("Unauthorized", status=403)

    filter_by = request.GET.get("filter", "week")
    if filter_by not in COOK_HISTORY_FILTERS:
        filter_by = "week"

    orders = get_cook_history_orders(filter_by)

    return render(
        request,
        "cook_order_history.html",
        {
            "orders": orders,
            "active_filter": filter_by,
        },
    )


@require_GET
def healthcheck_view(request):
    return JsonResponse({"ok": True, "status": "healthy"})


@require_GET
def my_orders_api_view(request):
    try:
        user, _ = get_authenticated_user_from_bearer(request, expected_type="access")
    except JWTValidationError as exc:
        return json_error(str(exc), status=401)

    queryset = Order.objects.filter(user=user).prefetch_related("items__menu_item")
    for order in queryset:
        sync_order_workflow(order)
    queryset = Order.objects.filter(user=user).prefetch_related("items__menu_item")
    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    page_obj = paginate_queryset(request, queryset, default=10)
    return JsonResponse({"ok": True, **build_paginated_response(page_obj, serialize_order)})


@csrf_exempt
@rate_limit(
    key_prefix="order-status-update",
    limit=PAYMENT_RATE_LIMIT_COUNT,
    window_seconds=PAYMENT_RATE_LIMIT_WINDOW_SECONDS,
)
def order_status_api_view(request, order_id):
    if request.method != "PATCH":
        return json_error("Only PATCH is allowed.", status=405)

    try:
        user, _ = get_authenticated_user_from_bearer(request, expected_type="access")
        payload = get_json_payload(request)
    except ValidationError as exc:
        return json_error(str(exc), status=400)
    except JWTValidationError as exc:
        return json_error(str(exc), status=401)

    if not user.is_staff:
        return json_error("You are not authorized to update order status.", status=403)

    order = get_object_or_404(Order.objects.prefetch_related("items__menu_item"), pk=order_id)
    new_status = payload.get("status", "").strip()
    if new_status not in {choice[0] for choice in Order.STATUS_CHOICES}:
        return json_error("Invalid status.", status=400)

    try:
        if new_status == "accepted":
            prep_minutes = parse_minutes(payload.get("prep_minutes"))
            if prep_minutes not in INITIAL_PREP_MINUTE_OPTIONS:
                return json_error("Choose a prep time of 15, 20, or 25 minutes.", status=400)
            if not order.can_transition_to("accepted"):
                return json_error(f"Cannot move order from {order.status} to accepted.", status=400)
            order.status = "accepted"
            order.cook_prep_minutes = prep_minutes
            order.cook_extra_minutes = 0
            order.cook_prep_time = f"{prep_minutes} min"
            order.cook_prep_started_at = timezone.now()
            order.save()
        else:
            order.transition_to(new_status)
    except ValidationError as exc:
        return json_error(exc.message, status=400)

    return JsonResponse({"ok": True, "order": serialize_order(order)})


@csrf_exempt
@rate_limit(
    key_prefix="razorpay-order-create",
    limit=PAYMENT_RATE_LIMIT_COUNT,
    window_seconds=PAYMENT_RATE_LIMIT_WINDOW_SECONDS,
)
def razorpay_order_api_view(request):
    if request.method != "POST":
        return json_error("Only POST is allowed.", status=405)

    try:
        user, _ = get_authenticated_user_from_bearer(request, expected_type="access")
        payload = get_json_payload(request)
    except ValidationError as exc:
        return json_error(str(exc), status=400)
    except JWTValidationError as exc:
        return json_error(str(exc), status=401)

    order = get_object_or_404(
        Order.objects.filter(user=user).prefetch_related("items__menu_item"),
        pk=payload.get("order_id"),
    )

    try:
        razorpay_order = create_and_attach_razorpay_order_for_order(order=order, user=user)
    except ValidationError as exc:
        return json_error(str(exc), status=409)
    except RazorpayConfigurationError as exc:
        return json_error(str(exc), status=503)
    except RazorpayAPIError as exc:
        return json_error(str(exc), status=502)

    return JsonResponse(
        {
            "ok": True,
            "razorpay_order": razorpay_order,
            "order": serialize_order(order),
        }
    )


@login_required(login_url="/profile/?mode=login")
@rate_limit(
    key_prefix="razorpay-order-create-session",
    limit=PAYMENT_RATE_LIMIT_COUNT,
    window_seconds=PAYMENT_RATE_LIMIT_WINDOW_SECONDS,
)
def razorpay_order_session_view(request):
    if request.method != "POST":
        return json_error("Only POST is allowed.", status=405)

    try:
        payload = get_json_payload(request)
    except ValidationError as exc:
        return json_error(str(exc), status=400)

    order = get_object_or_404(
        Order.objects.filter(user=request.user).prefetch_related("items__menu_item"),
        pk=payload.get("order_id"),
    )

    try:
        razorpay_order = create_and_attach_razorpay_order_for_order(order=order, user=request.user)
    except ValidationError as exc:
        return json_error(str(exc), status=409)
    except RazorpayConfigurationError as exc:
        return json_error(str(exc), status=503)
    except RazorpayAPIError as exc:
        return json_error(str(exc), status=502)

    return JsonResponse(
        {
            "ok": True,
            "razorpay_order": razorpay_order,
            "order": serialize_order(order),
        }
    )


@csrf_exempt
@rate_limit(
    key_prefix="razorpay-webhook",
    limit=PAYMENT_RATE_LIMIT_COUNT,
    window_seconds=PAYMENT_RATE_LIMIT_WINDOW_SECONDS,
)
def razorpay_webhook_view(request):
    if request.method != "POST":
        return json_error("Only POST is allowed.", status=405)

    payload_bytes = request.body or b""
    signature = request.headers.get("X-Razorpay-Signature", "").strip()

    try:
        verify_razorpay_webhook_signature(payload_bytes=payload_bytes, signature=signature)
        payload = json.loads(payload_bytes.decode("utf-8") or "{}")
    except RazorpayConfigurationError as exc:
        return json_error(str(exc), status=503)
    except RazorpayAPIError as exc:
        return json_error(str(exc), status=401)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json_error("Invalid webhook payload.", status=400)

    event_type = payload.get("event", "").strip()
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    razorpay_payment_id = (payment_entity.get("id") or "").strip()
    razorpay_order_id = (payment_entity.get("order_id") or "").strip()

    if not razorpay_order_id:
        return JsonResponse({"ok": True, "message": "No order reference in webhook payload."})

    event_id = request.headers.get("X-Razorpay-Event-Id", "").strip()
    if not event_id:
        event_id = f"{event_type}:{razorpay_payment_id or razorpay_order_id}"

    order = (
        Order.objects.filter(payment_provider="razorpay", payment_reference=razorpay_order_id)
        .prefetch_related("items__menu_item")
        .first()
    )
    if order is None:
        return JsonResponse({"ok": True, "message": "No matching order for webhook."})

    if PaymentEvent.objects.filter(provider_event_id=event_id).exists():
        return JsonResponse({"ok": True, "message": "Webhook already processed."})

    with transaction.atomic():
        PaymentEvent.objects.create(
            order=order,
            provider="razorpay",
            provider_event_id=event_id,
            event_type=event_type or "unknown",
            payment_id=razorpay_payment_id,
            payload=payload,
        )

        if event_type == "payment.captured":
            order.mark_payment("razorpay", razorpay_order_id)
        elif event_type == "payment.failed":
            order.mark_payment_failed("razorpay", razorpay_order_id)

    return JsonResponse({"ok": True, "message": "Webhook processed."})


@csrf_exempt
@rate_limit(
    key_prefix="auth-signup",
    limit=AUTH_RATE_LIMIT_COUNT,
    window_seconds=AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
def jwt_signup_view(request):
    if request.method != "POST":
        return json_error("Only POST is allowed.", status=405)

    try:
        payload = get_json_payload(request)
    except ValidationError as exc:
        return json_error(str(exc), status=400)

    signup_form = SignUpForm(
        {
            "username": payload.get("username", ""),
            "full_name": payload.get("full_name", ""),
            "email": payload.get("email", ""),
            "phone": payload.get("phone", ""),
            "address": payload.get("address", ""),
            "password1": payload.get("password", ""),
            "password2": payload.get("confirm_password", payload.get("password", "")),
        }
    )

    if not signup_form.is_valid():
        return JsonResponse({"ok": False, "errors": signup_form.errors}, status=400)

    user = signup_form.save()
    tokens = build_token_pair(user)
    profile = user.profile
    return JsonResponse(
        {
            "ok": True,
            "message": "User registered successfully.",
            "tokens": tokens,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "full_name": profile.full_name,
                "phone": profile.phone,
                "address": profile.address,
                "is_staff": user.is_staff,
            },
        },
        status=201,
    )


@csrf_exempt
@rate_limit(
    key_prefix="auth-login",
    limit=AUTH_RATE_LIMIT_COUNT,
    window_seconds=AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
def jwt_login_view(request):
    if request.method != "POST":
        return json_error("Only POST is allowed.", status=405)

    try:
        payload = get_json_payload(request)
    except ValidationError as exc:
        return json_error(str(exc), status=400)

    user = authenticate(
        request,
        username=payload.get("username", "").strip(),
        password=payload.get("password", ""),
    )
    if user is None:
        return json_error("Invalid username or password.", status=401)

    tokens = build_token_pair(user)
    profile = getattr(user, "profile", None)
    return JsonResponse(
        {
            "ok": True,
            "message": "Login successful.",
            "tokens": tokens,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "full_name": profile.full_name if profile else "",
                "phone": profile.phone if profile else "",
                "address": profile.address if profile else "",
                "is_staff": user.is_staff,
            },
        }
    )


@csrf_exempt
@rate_limit(
    key_prefix="auth-refresh",
    limit=AUTH_RATE_LIMIT_COUNT,
    window_seconds=AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
def jwt_refresh_view(request):
    if request.method != "POST":
        return json_error("Only POST is allowed.", status=405)

    try:
        payload = get_json_payload(request)
        user, _ = get_user_from_token(payload.get("refresh", ""), expected_type="refresh")
    except ValidationError as exc:
        return json_error(str(exc), status=400)
    except JWTValidationError as exc:
        return json_error(str(exc), status=401)

    tokens = build_token_pair(user)
    return JsonResponse(
        {
            "ok": True,
            "message": "Token refreshed successfully.",
            "tokens": tokens,
        }
    )


def jwt_me_view(request):
    if request.method != "GET":
        return json_error("Only GET is allowed.", status=405)

    try:
        user, _ = get_authenticated_user_from_bearer(request, expected_type="access")
    except JWTValidationError as exc:
        return json_error(str(exc), status=401)

    profile = getattr(user, "profile", None)
    return JsonResponse(
        {
            "ok": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "full_name": profile.full_name if profile else "",
                "phone": profile.phone if profile else "",
                "address": profile.address if profile else "",
                "is_staff": user.is_staff,
            },
        }
    )


@csrf_exempt
def jwt_logout_view(request):
    if request.method != "POST":
        return json_error("Only POST is allowed.", status=405)

    return JsonResponse(
        {
            "ok": True,
            "message": "JWT logout is handled client-side. Remove stored tokens on the client.",
        }
    )

def profile_view(request):
    if request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
        if profile is None:
            profile = Profile.objects.create(user=request.user, full_name="", phone="", address="")

        mode = request.GET.get("mode", "view")

        if request.method == "POST":
            action = request.POST.get("action")

            if action == "update_profile":
                profile_form = ProfileForm(request.POST, request.FILES, instance=profile)
                if profile_form.is_valid():
                    profile_form.save()
                    messages.success(request, "Profile updated successfully.")
                    return redirect("profile_view")
                else:
                    messages.error(request, "Please fix the highlighted errors.")

            elif action == "delete_photo":
                if profile.profile_photo:
                    try:
                        profile.profile_photo.delete(save=False)
                    except PermissionError:
                        pass
                    profile.profile_photo = None
                    profile.save()
                    messages.success(request, "Profile photo removed.")
                else:
                    messages.info(request, "No profile photo to remove.")
                return redirect("profile_view")

            else:
                profile_form = ProfileForm(instance=profile)
        else:
            profile_form = ProfileForm(instance=profile)

        return render(
            request,
            'profile.html',
            {
                "last_order": request.session.get("last_order"),
                "profile": profile,
                "profile_form": profile_form,
                "mode": mode,
            },
        )

    signup_form = SignUpForm()
    login_form = AuthenticationForm(request)
    mode = request.GET.get("mode", "signup")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "signup":
            signup_form = SignUpForm(request.POST, request.FILES)
            login_form = AuthenticationForm(request)
            mode = "signup"
            if signup_form.is_valid():
                user = signup_form.save()
                login(request, user)
                if user.is_staff:
                    return redirect("cook_orders_view")
                return redirect("home_view")

        if action == "login":
            login_form = AuthenticationForm(request, data=request.POST)
            signup_form = SignUpForm()
            mode = "login"
            if login_form.is_valid():
                user = login_form.get_user()
                login(request, user)
                if user.is_staff:
                    return redirect("cook_orders_view")
                return redirect("home_view")

    return render(
        request,
        'profile.html',
        {
            "signup_form": signup_form,
            "login_form": login_form,
            "mode": mode,
        },
    )

def logout_view(request):
    # Preserve recent order context so user can still view last order after logout
    last_order_id = request.session.get("last_order_id")
    logout(request)
    if last_order_id:
        request.session["last_order_id"] = last_order_id
    return redirect("/profile/?mode=login")


def handler400(request, exception):
    return error_page(request, 400, "Bad request", "The request could not be processed.")


def handler403(request, exception):
    return error_page(request, 403, "Access denied", "You do not have permission to open this page.")


def handler404(request, exception):
    return error_page(request, 404, "Page not found", "The page you requested does not exist.")


def handler500(request):
    return error_page(request, 500, "Server error", "Something went wrong on our side. Please try again shortly.")
