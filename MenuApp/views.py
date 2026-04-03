import re
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, HttpResponse
from django.utils import timezone
from .forms import SignUpForm, ProfileForm
from .models import MenuItem, Profile, Order, OrderItem


DELIVERY_FEE = Decimal("35.00")
TAX_RATE = Decimal("0.05")
ORDER_PAGE_HISTORY_HOURS = 2
INITIAL_PREP_MINUTE_OPTIONS = (15, 20, 25)
EXTRA_PREP_MINUTE_OPTIONS = (5, 10)
COOK_HISTORY_FILTERS = ("week", "month", "year")
RESTAURANT_PHONE_DISPLAY = "+91 9079172810"
RESTAURANT_PHONE_LINK = "+919079172810"


def parse_price(price: str) -> Decimal:
    cleaned_price = re.sub(r"[^\d.]", "", price or "")
    if not cleaned_price:
        return Decimal("0.00")

    try:
        return Decimal(cleaned_price)
    except InvalidOperation:
        return Decimal("0.00")


def build_cart_items(cart):
    item_ids = [int(item_id) for item_id in cart.keys()]
    menu_items = MenuItem.objects.in_bulk(item_ids)
    cart_items = []
    subtotal = Decimal("0.00")

    for item_id, quantity in cart.items():
        menu_item = menu_items.get(int(item_id))
        if not menu_item:
            continue

        unit_price = parse_price(menu_item.price)
        line_total = unit_price * quantity
        subtotal += line_total
        cart_items.append(
            {
                "item": menu_item,
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
        item.cart_quantity = cart.get(str(item.id), 0)
        grouped_menu.setdefault(item.category, []).append(item)

    return render(request, 'menu.html', {
        'grouped_menu': grouped_menu,
        'cart_added': cart_added,
    })

def cart_view(request):
    cart = request.session.get("cart", {})

    if request.method == "POST":
        action = request.POST.get("action")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        def cart_json_response(item=None):
            item_key = str(item.pk) if item else request.POST.get("item_id", "")
            return JsonResponse(
                {
                    "ok": True,
                    "item_name": item.name if item else "",
                    "cart_count": sum(cart.values()),
                    "item_count": cart.get(item_key, 0),
                }
            )

        if action == "add":
            item = get_object_or_404(MenuItem, pk=request.POST.get("item_id"))
            item_key = str(item.pk)
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
            item_key = request.POST.get("item_id")
            if item_key in cart:
                cart[item_key] += 1
                request.session["cart"] = cart
                request.session.modified = True
            if is_ajax:
                item = get_object_or_404(MenuItem, pk=item_key)
                return cart_json_response(item)
            return redirect("cart_view")

        if action == "decrease":
            item_key = request.POST.get("item_id")
            item = get_object_or_404(MenuItem, pk=item_key)
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
            item_key = request.POST.get("item_id")
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

            order = Order.objects.create(
                user=request.user if request.user.is_authenticated else None,
                receiver_name=receiver_name,
                receiver_phone=receiver_phone,
                receiver_address=receiver_address,
                subtotal=subtotal,
                delivery_fee=DELIVERY_FEE if cart_items else Decimal("0.00"),
                tax_amount=tax_amount,
                grand_total=grand_total,
            )

            for cart_item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    menu_item=cart_item["item"],
                    quantity=cart_item["quantity"],
                    unit_price=cart_item["unit_price"],
                    line_total=cart_item["line_total"],
                )

            request.session["last_order_id"] = order.id
            request.session["cart"] = {}
            request.session.modified = True
            return redirect("order_view")

    if request.GET.get("add"):
        item = get_object_or_404(MenuItem, pk=request.GET.get("add"))
        item_key = str(item.pk)
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
    return render(
        request,
        'order.html',
        {
            "last_order": order,
            "order_page_history_hours": ORDER_PAGE_HISTORY_HOURS,
            "restaurant_phone_display": RESTAURANT_PHONE_DISPLAY,
            "restaurant_phone_link": RESTAURANT_PHONE_LINK,
        },
    )


def my_orders_view(request):
    if not request.user.is_authenticated:
        return redirect("/profile/?mode=login")

    orders = (
        Order.objects.filter(user=request.user)
        .prefetch_related("items__menu_item")
        .order_by("-created_at")
    )
    return render(request, "my_orders.html", {"orders": orders})


def order_detail_view(request, order_id):
    order = get_visible_order_for_request(request, order_id=order_id)
    return render(
        request,
        "order.html",
        {
            "last_order": order,
            "is_detail_view": True,
            "restaurant_phone_display": RESTAURANT_PHONE_DISPLAY,
            "restaurant_phone_link": RESTAURANT_PHONE_LINK,
        },
    )


def cook_orders_view(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return HttpResponse("Unauthorized", status=403)

    if request.method == "POST":
        order_id = request.POST.get("order_id")
        action = request.POST.get("action")

        order = Order.objects.filter(pk=order_id).first()
        if order:
            if action == "accept":
                prep_minutes = parse_minutes(request.POST.get("prep_minutes"))
                if prep_minutes not in INITIAL_PREP_MINUTE_OPTIONS:
                    messages.error(request, "Choose a prep time of 15, 20, or 25 minutes.")
                else:
                    order.status = "accepted"
                    order.cook_prep_minutes = prep_minutes
                    order.cook_extra_minutes = 0
                    order.cook_prep_time = f"{prep_minutes} min"
                    order.cook_prep_started_at = timezone.now()
                    order.save()
                    messages.success(request, f"Order #{order.id} accepted for {prep_minutes} minutes.")

            elif action == "start_preparing":
                order.status = "preparing"
                order.save(update_fields=["status", "updated_at"])
                messages.success(request, f"Order #{order.id} preparation started.")

            elif action == "ready":
                order.status = "ready"
                order.save(update_fields=["status", "updated_at"])
                messages.success(request, f"Order #{order.id} marked ready for pickup.")

            elif action == "delivered":
                order.status = "completed"
                order.save(update_fields=["status", "updated_at"])
                messages.success(request, f"Order #{order.id} marked delivered.")

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
    today_orders = Order.objects.filter(created_at__date=today).order_by("-created_at")
    today_count = today_orders.count()
    today_value = today_orders.aggregate(total=Sum('grand_total'))['total'] or 0

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
