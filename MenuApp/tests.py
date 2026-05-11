import json
import hashlib
import hmac
import shutil
from datetime import timedelta
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch
from django.core.exceptions import ValidationError

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from .models import MenuItem, PaymentEvent, Profile, Order, OrderItem
from .services.image_api import fetch_unsplash_image


class MenuItemModelTests(TestCase):
    def test_image_url_returns_external_url(self):
        item = MenuItem(
            name="Pizza",
            category="Pizza",
            price="100",
            description="Test",
            image="https://example.com/pizza.jpg",
        )

        self.assertEqual(item.image_url, "https://example.com/pizza.jpg")

    def test_image_url_converts_local_static_path(self):
        item = MenuItem(
            name="Burger",
            category="Burgers",
            price="80",
            description="Test",
            image="images/burger.jpg",
        )

        self.assertEqual(item.image_url, static("images/burger.jpg"))

    def test_image_url_uses_media_when_file_exists_in_media_root(self):
        relative_path = "images/test-media.jpg"
        temp_media_root = settings.BASE_DIR / "test_media"
        shutil.rmtree(temp_media_root, ignore_errors=True)

        try:
            with override_settings(MEDIA_ROOT=temp_media_root):
                media_file = settings.MEDIA_ROOT / relative_path
                media_file.parent.mkdir(parents=True, exist_ok=True)
                media_file.write_bytes(b"test")

                item = MenuItem(
                    name="Uploaded Item",
                    category="Uploads",
                    price="90",
                    description="Test",
                    image=relative_path,
                )

                self.assertEqual(item.image_url, f"{settings.MEDIA_URL}{relative_path}")
        finally:
            shutil.rmtree(temp_media_root, ignore_errors=True)


class OrderModelTests(TestCase):
    def test_order_rejects_incorrect_totals(self):
        order = Order(
            receiver_name="Test User",
            receiver_phone="9876543210",
            receiver_address="Street 1",
            subtotal="100.00",
            delivery_fee="35.00",
            tax_amount="5.00",
            grand_total="120.00",
        )

        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_order_status_transition_rules(self):
        order = Order.objects.create(
            receiver_name="Test User",
            receiver_phone="9876543210",
            receiver_address="Street 1",
            subtotal="100.00",
            delivery_fee="35.00",
            tax_amount="5.00",
            grand_total="140.00",
        )

        self.assertTrue(order.can_transition_to("accepted"))
        order.transition_to("accepted")
        self.assertEqual(order.status, "accepted")
        self.assertFalse(order.can_transition_to("completed"))

    def test_order_item_validates_line_total(self):
        burger = MenuItem.objects.create(
            name="Burger",
            category="Burgers",
            price="90",
            description="Loaded from test",
            image="images/burger.jpg",
        )
        order = Order.objects.create(
            receiver_name="Test User",
            receiver_phone="9876543210",
            receiver_address="Street 1",
            subtotal="100.00",
            delivery_fee="35.00",
            tax_amount="5.00",
            grand_total="140.00",
        )
        item = OrderItem(
            order=order,
            menu_item=burger,
            quantity=2,
            unit_price="90.00",
            line_total="100.00",
        )

        with self.assertRaises(ValidationError):
            item.full_clean()


class ImageApiTests(TestCase):
    @patch("MenuApp.services.image_api.urlopen")
    def test_fetch_unsplash_image_returns_first_result(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            b'{"results":[{"urls":{"small":"https://images.example.com/pizza.jpg"}}]}'
        )

        image_url = fetch_unsplash_image("Pizza", access_key="test-key")

        self.assertEqual(image_url, "https://images.example.com/pizza.jpg")

    def test_fetch_unsplash_image_returns_none_without_api_key(self):
        image_url = fetch_unsplash_image("Pizza", access_key="")

        self.assertIsNone(image_url)

    @patch("MenuApp.services.image_api.urlopen")
    def test_fetch_unsplash_image_returns_none_on_network_failure(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("boom")

        image_url = fetch_unsplash_image("Pizza", access_key="test-key")

        self.assertIsNone(image_url)

    @patch("MenuApp.management.commands.fetch_menu_images.fetch_unsplash_image")
    def test_fetch_menu_images_command_updates_database(self, mock_fetch_image):
        mock_fetch_image.return_value = "https://images.example.com/burger.jpg"
        item = MenuItem.objects.create(
            name="Burger",
            category="Burgers",
            price="90",
            description="Loaded from test",
            image="images/burger.jpg",
        )

        call_command("fetch_menu_images")
        item.refresh_from_db()

        self.assertEqual(item.image, "https://images.example.com/burger.jpg")


class ViewTests(TestCase):
    def setUp(self):
        MenuItem.objects.create(
            name="Veg Pizza",
            category="Pizza",
            price="120",
            description="Loaded from test",
            image="https://example.com/pizza.jpg",
        )
        MenuItem.objects.create(
            name="Burger",
            category="Burgers",
            price="90",
            description="Loaded from test",
            image="images/burger.jpg",
        )

    def test_home_view_renders(self):
        response = self.client.get(reverse("home_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scan, sign in, and order with Shubham's Kitchen")

    def test_home_view_shows_menu_to_logged_in_user(self):
        user = User.objects.create_user(username="shubham", password="StrongPass123!")
        self.client.force_login(user)

        response = self.client.get(reverse("home_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your neighborhood dhaba meets café")
        self.assertContains(response, "A: Aloo Paratha, B: Bhaji Pav, C: Chai")

    def test_contact_view_renders(self):
        response = self.client.get(reverse("contact_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contact Shubham's Kitchen")
        self.assertContains(response, "Restaurant timings")

    def test_menu_view_renders(self):
        response = self.client.get(reverse("menu_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Veg Pizza")
        self.assertContains(response, "Burger")

    def test_menu_view_shows_existing_item_count(self):
        burger = MenuItem.objects.get(name="Burger")
        session = self.client.session
        session["cart"] = {str(burger.id): 3}
        session.save()

        response = self.client.get(reverse("menu_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "In cart:")
        self.assertContains(response, ">3<", html=False)

    def test_menu_add_to_cart_stays_on_menu_page(self):
        burger = MenuItem.objects.get(name="Burger")

        response = self.client.post(
            reverse("cart_view"),
            {"action": "add", "item_id": burger.id, "next_url": "menu"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/menu/?cart_added=1")
        self.assertEqual(self.client.session["cart"][str(burger.id)], 1)

    def test_menu_add_to_cart_ajax_returns_json_without_redirect(self):
        burger = MenuItem.objects.get(name="Burger")

        response = self.client.post(
            reverse("cart_view"),
            {"action": "add", "item_id": burger.id, "next_url": "menu"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["item_name"], "Burger")
        self.assertEqual(response.json()["cart_count"], 1)
        self.assertEqual(response.json()["item_count"], 1)
        self.assertEqual(self.client.session["cart"][str(burger.id)], 1)

    def test_menu_decrease_item_ajax_returns_zero_when_removed(self):
        burger = MenuItem.objects.get(name="Burger")
        session = self.client.session
        session["cart"] = {str(burger.id): 1}
        session.save()

        response = self.client.post(
            reverse("cart_view"),
            {"action": "decrease", "item_id": burger.id},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cart_count"], 0)
        self.assertEqual(response.json()["item_count"], 0)
        self.assertNotIn(str(burger.id), self.client.session["cart"])

    def test_menu_increase_item_ajax_updates_quantity(self):
        burger = MenuItem.objects.get(name="Burger")
        session = self.client.session
        session["cart"] = {str(burger.id): 2}
        session.save()

        response = self.client.post(
            reverse("cart_view"),
            {"action": "increase", "item_id": burger.id},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cart_count"], 3)
        self.assertEqual(response.json()["item_count"], 3)
        self.assertEqual(self.client.session["cart"][str(burger.id)], 3)

    def test_cart_view_adds_item_to_session_cart(self):
        burger = MenuItem.objects.get(name="Burger")

        response = self.client.get(f"{reverse('cart_view')}?add={burger.id}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("cart_view"))
        self.assertEqual(self.client.session["cart"][str(burger.id)], 1)

    def test_cart_view_updates_quantities_and_totals(self):
        burger = MenuItem.objects.get(name="Burger")
        session = self.client.session
        session["cart"] = {str(burger.id): 2}
        session.save()

        response = self.client.get(reverse("cart_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your cart")
        self.assertContains(response, "180.00")

    def test_cart_view_places_order_and_clears_cart(self):
        burger = MenuItem.objects.get(name="Burger")
        session = self.client.session
        session["cart"] = {str(burger.id): 1}
        session.save()

        response = self.client.post(
            reverse("cart_view"),
            {
                "action": "place_order",
                "receiver_name": "Test Receiver",
                "receiver_phone": "9876543210",
                "receiver_address": "Test Street",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["cart"], {})

    def test_cart_view_places_pay_now_order_with_payment_window(self):
        user = User.objects.create_user(username="paycartuser", password="StrongPass123!")
        burger = MenuItem.objects.get(name="Burger")
        session = self.client.session
        session["cart"] = {str(burger.id): 1}
        session.save()

        self.client.force_login(user)
        response = self.client.post(
            reverse("cart_view"),
            {
                "action": "place_order",
                "receiver_name": "Pay Receiver",
                "receiver_phone": "9876543210",
                "receiver_address": "Pay Street",
                "payment_method": "razorpay",
            },
        )

        self.assertEqual(response.status_code, 302)
        order = Order.objects.latest("id")
        self.assertEqual(order.payment_method, "razorpay")
        self.assertIsNone(order.confirmed_at)
        self.assertIsNotNone(order.payment_due_at)

    def test_cart_view_rejects_blank_receiver_details(self):
        burger = MenuItem.objects.get(name="Burger")
        session = self.client.session
        session["cart"] = {str(burger.id): 1}
        session.save()

        response = self.client.post(
            reverse("cart_view"),
            {
                "action": "place_order",
                "receiver_name": "",
                "receiver_phone": "",
                "receiver_address": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Receiver name is required.")
        self.assertContains(response, "Phone number is required.")
        self.assertContains(response, "Address is required.")
        self.assertEqual(Order.objects.count(), 0)

    def test_order_view_shows_last_placed_order_only(self):
        # Create a real order and store ID in session for display
        order = Order.objects.create(
            receiver_name="Test Receiver",
            receiver_phone="9876543210",
            receiver_address="Test Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        burger = MenuItem.objects.get(name="Burger")
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        session = self.client.session
        session["last_order_id"] = order.id
        session.save()

        response = self.client.get(reverse("order_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Placed order details")
        self.assertContains(response, "Total paid")
        self.assertContains(response, "Burger")
        self.assertContains(response, "Rs. 129.50")

    def test_my_orders_view_lists_only_logged_in_users_orders(self):
        owner = User.objects.create_user(username="owner", password="StrongPass123!")
        other_user = User.objects.create_user(username="other", password="StrongPass123!")
        burger = MenuItem.objects.get(name="Burger")

        owner_order = Order.objects.create(
            user=owner,
            receiver_name="Owner Receiver",
            receiver_phone="9876543210",
            receiver_address="Owner Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            status="accepted",
        )
        OrderItem.objects.create(order=owner_order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        other_order = Order.objects.create(
            user=other_user,
            receiver_name="Other Receiver",
            receiver_phone="9999999999",
            receiver_address="Other Street",
            subtotal="180.00",
            delivery_fee="35.00",
            tax_amount="9.00",
            grand_total="224.00",
        )
        OrderItem.objects.create(order=other_order, menu_item=burger, quantity=2, unit_price="90.00", line_total="180.00")

        self.client.force_login(owner)
        response = self.client.get(reverse("my_orders_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My orders")
        self.assertContains(response, f"Order #{owner_order.id}")
        self.assertNotContains(response, f"Order #{other_order.id}")
        self.assertContains(response, "Accepted")

    def test_my_orders_view_redirects_anonymous_users_to_login(self):
        response = self.client.get(reverse("my_orders_view"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/profile/?mode=login")

    def test_order_detail_view_requires_ownership(self):
        owner = User.objects.create_user(username="owner2", password="StrongPass123!")
        intruder = User.objects.create_user(username="intruder", password="StrongPass123!")
        burger = MenuItem.objects.get(name="Burger")
        order = Order.objects.create(
            user=owner,
            receiver_name="Owner Receiver",
            receiver_phone="9876543210",
            receiver_address="Owner Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        self.client.force_login(intruder)
        response = self.client.get(reverse("order_detail_view", args=[order.id]))

        self.assertEqual(response.status_code, 404)

    def test_order_detail_view_shows_selected_order_for_owner(self):
        user = User.objects.create_user(username="detailuser", password="StrongPass123!")
        burger = MenuItem.objects.get(name="Burger")
        order = Order.objects.create(
            user=user,
            receiver_name="Detail Receiver",
            receiver_phone="9876543210",
            receiver_address="Detail Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            status="ready",
        )
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        self.client.force_login(user)
        response = self.client.get(reverse("order_detail_view", args=[order.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order details")
        self.assertContains(response, f"Order #{order.id}")
        self.assertContains(response, "Placed on")
        self.assertContains(response, "View all orders")
        self.assertContains(response, 'tel:+919079172810', html=False)

    def test_cook_accept_order_sets_timer_and_status(self):
        cook = User.objects.create_user(username="cook", password="StrongPass123!", is_staff=True)
        burger = MenuItem.objects.get(name="Burger")
        order = Order.objects.create(
            receiver_name="Cook Receiver",
            receiver_phone="9876543210",
            receiver_address="Kitchen Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        self.client.force_login(cook)
        response = self.client.post(
            reverse("cook_orders_view"),
            {"order_id": order.id, "action": "accept", "prep_minutes": "20"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "accepted")
        self.assertEqual(order.cook_prep_minutes, 20)
        self.assertEqual(order.cook_extra_minutes, 0)
        self.assertEqual(order.cook_prep_time, "20 min")
        self.assertIsNotNone(order.cook_prep_started_at)

    def test_cook_start_preparing_and_add_time_flow(self):
        cook = User.objects.create_user(username="cook2", password="StrongPass123!", is_staff=True)
        burger = MenuItem.objects.get(name="Burger")
        order = Order.objects.create(
            receiver_name="Cook Receiver",
            receiver_phone="9876543210",
            receiver_address="Kitchen Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            status="accepted",
            cook_prep_minutes=15,
            cook_extra_minutes=0,
            cook_prep_time="15 min",
            cook_prep_started_at=timezone.now(),
        )
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        self.client.force_login(cook)
        self.client.post(
            reverse("cook_orders_view"),
            {"order_id": order.id, "action": "add_time", "extra_minutes": "5"},
        )
        order.refresh_from_db()
        self.assertEqual(order.cook_extra_minutes, 5)
        self.assertEqual(order.cook_prep_time, "20 min")

        self.client.post(
            reverse("cook_orders_view"),
            {"order_id": order.id, "action": "start_preparing"},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, "preparing")

    def test_cook_ready_and_delivered_flow(self):
        cook = User.objects.create_user(username="cook3", password="StrongPass123!", is_staff=True)
        burger = MenuItem.objects.get(name="Burger")
        order = Order.objects.create(
            receiver_name="Cook Receiver",
            receiver_phone="9876543210",
            receiver_address="Kitchen Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            status="preparing",
            cook_prep_minutes=25,
            cook_prep_time="25 min",
            cook_prep_started_at=timezone.now(),
        )
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        self.client.force_login(cook)
        self.client.post(
            reverse("cook_orders_view"),
            {"order_id": order.id, "action": "ready"},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, "ready")

        self.client.post(
            reverse("cook_orders_view"),
            {"order_id": order.id, "action": "delivered"},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, "completed")

    def test_cook_dashboard_shows_only_today_orders(self):
        cook = User.objects.create_user(username="cook4", password="StrongPass123!", is_staff=True)
        burger = MenuItem.objects.get(name="Burger")
        today_order = Order.objects.create(
            receiver_name="Today Receiver",
            receiver_phone="9876543210",
            receiver_address="Today Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            confirmed_at=timezone.now(),
        )
        old_order = Order.objects.create(
            receiver_name="Old Receiver",
            receiver_phone="9876543210",
            receiver_address="Old Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        OrderItem.objects.create(order=today_order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")
        OrderItem.objects.create(order=old_order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")
        Order.objects.filter(pk=old_order.pk).update(created_at=timezone.now() - timedelta(days=2))

        self.client.force_login(cook)
        response = self.client.get(reverse("cook_orders_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Order #{today_order.id}")
        self.assertNotContains(response, f"Order #{old_order.id}")
        self.assertContains(response, "Showing only today's confirmed active orders")
        self.assertContains(response, f'tel:{today_order.receiver_phone}', html=False)

    def test_cook_order_history_week_filter_excludes_today_and_old_orders(self):
        cook = User.objects.create_user(username="cook5", password="StrongPass123!", is_staff=True)
        burger = MenuItem.objects.get(name="Burger")
        today_order = Order.objects.create(
            receiver_name="Today Receiver",
            receiver_phone="9876543210",
            receiver_address="Today Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        week_order = Order.objects.create(
            receiver_name="Week Receiver",
            receiver_phone="9876543210",
            receiver_address="Week Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        month_order = Order.objects.create(
            receiver_name="Month Receiver",
            receiver_phone="9876543210",
            receiver_address="Month Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        for order in (today_order, week_order, month_order):
            OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        Order.objects.filter(pk=week_order.pk).update(created_at=timezone.now() - timedelta(days=3))
        Order.objects.filter(pk=month_order.pk).update(created_at=timezone.now() - timedelta(days=20))

        self.client.force_login(cook)
        response = self.client.get(reverse("cook_order_history_view"), {"filter": "week"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order History")
        self.assertContains(response, f"Order #{week_order.id}")
        self.assertNotContains(response, f"Order #{today_order.id}")
        self.assertNotContains(response, f"Order #{month_order.id}")

    def test_cook_order_history_month_and_year_filters(self):
        cook = User.objects.create_user(username="cook6", password="StrongPass123!", is_staff=True)
        burger = MenuItem.objects.get(name="Burger")
        month_order = Order.objects.create(
            receiver_name="Month Receiver",
            receiver_phone="9876543210",
            receiver_address="Month Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        year_order = Order.objects.create(
            receiver_name="Year Receiver",
            receiver_phone="9876543210",
            receiver_address="Year Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        very_old_order = Order.objects.create(
            receiver_name="Very Old Receiver",
            receiver_phone="9876543210",
            receiver_address="Very Old Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )
        for order in (month_order, year_order, very_old_order):
            OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        Order.objects.filter(pk=month_order.pk).update(created_at=timezone.now() - timedelta(days=20))
        Order.objects.filter(pk=year_order.pk).update(created_at=timezone.now() - timedelta(days=200))
        Order.objects.filter(pk=very_old_order.pk).update(created_at=timezone.now() - timedelta(days=500))

        self.client.force_login(cook)
        month_response = self.client.get(reverse("cook_order_history_view"), {"filter": "month"})
        year_response = self.client.get(reverse("cook_order_history_view"), {"filter": "year"})

        self.assertContains(month_response, f"Order #{month_order.id}")
        self.assertNotContains(month_response, f"Order #{year_order.id}")
        self.assertContains(year_response, f"Order #{month_order.id}")
        self.assertContains(year_response, f"Order #{year_order.id}")
        self.assertNotContains(year_response, f"Order #{very_old_order.id}")

    def test_order_view_persists_with_logout_for_last_order(self):
        user = User.objects.create_user(username="cookuser", password="StrongPass123!")
        order = Order.objects.create(
            user=user,
            receiver_name="Chef Receiver",
            receiver_phone="9998887776",
            receiver_address="Cook Street",
            subtotal="100.00",
            delivery_fee="30.00",
            tax_amount="5.00",
            grand_total="135.00",
        )
        burger = MenuItem.objects.get(name="Burger")
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="100.00", line_total="100.00")

        self.client.force_login(user)
        session = self.client.session
        session["last_order_id"] = order.id
        session.save()

        response = self.client.get(reverse("order_view"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Burger")

        self.client.get(reverse("logout_view"))

        response = self.client.get(reverse("order_view"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rs. 135.00")

    def test_order_view_hides_completed_order_after_time_limit(self):
        user = User.objects.create_user(username="timeduser", password="StrongPass123!")
        burger = MenuItem.objects.get(name="Burger")
        order = Order.objects.create(
            user=user,
            receiver_name="Timed Receiver",
            receiver_phone="9876543210",
            receiver_address="Timed Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            status="completed",
        )
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")
        old_time = timezone.now() - timedelta(hours=3)
        Order.objects.filter(pk=order.pk).update(updated_at=old_time)

        self.client.force_login(user)
        response = self.client.get(reverse("order_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No active order right now")
        self.assertContains(response, "View order history")

    def test_order_view_keeps_recently_completed_order_visible(self):
        user = User.objects.create_user(username="recentuser", password="StrongPass123!")
        burger = MenuItem.objects.get(name="Burger")
        order = Order.objects.create(
            user=user,
            receiver_name="Recent Receiver",
            receiver_phone="9876543210",
            receiver_address="Recent Street",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            status="completed",
        )
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")
        recent_time = timezone.now() - timedelta(minutes=30)
        Order.objects.filter(pk=order.pk).update(updated_at=recent_time)

        self.client.force_login(user)
        response = self.client.get(reverse("order_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Order #{order.id}")
        self.assertContains(response, "Delivered")


    def test_profile_view_shows_auth_forms_for_anonymous_user(self):
        response = self.client.get(reverse("profile_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your account or sign in")
        self.assertContains(response, "Sign up")
        self.assertContains(response, "Login")

    def test_profile_signup_creates_user_and_logs_in(self):
        response = self.client.post(
            reverse("profile_view"),
            {
                "action": "signup",
                "username": "shubham",
                "full_name": "Shubham Verma",
                "phone": "9876543210",
                "email": "shubham@example.com",
                "address": "Indore, Madhya Pradesh",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="shubham").exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), User.objects.get(username="shubham").id)
        profile = Profile.objects.get(user__username="shubham")
        self.assertEqual(profile.full_name, "Shubham Verma")
        self.assertEqual(profile.phone, "9876543210")
        self.assertEqual(profile.address, "Indore, Madhya Pradesh")

    def test_profile_signup_rejects_duplicate_email(self):
        User.objects.create_user(username="existing", password="StrongPass123!", email="same@example.com")

        response = self.client.post(
            reverse("profile_view"),
            {
                "action": "signup",
                "username": "newuser",
                "full_name": "New User",
                "phone": "9876543210",
                "email": "same@example.com",
                "address": "Indore",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists.")
        self.assertFalse(User.objects.filter(username="newuser").exists())

    def test_profile_view_shows_account_details_for_logged_in_user(self):
        user = User.objects.create_user(username="shubham", password="StrongPass123!")
        Profile.objects.create(user=user, full_name="Shubham Verma", phone="9876543210", address="Indore")
        self.client.force_login(user)

        response = self.client.get(reverse("profile_view"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Account details")
        self.assertContains(response, "Shubham Verma")
        self.assertContains(response, "9876543210")

    def test_profile_update_changes_data(self):
        user = User.objects.create_user(username="shubham", password="StrongPass123!")
        profile = Profile.objects.create(user=user, full_name="Shubham Verma", phone="9876543210", address="Indore")
        self.client.force_login(user)

        response = self.client.post(
            reverse("profile_view"),
            {
                "action": "update_profile",
                "full_name": "Shubham V.",
                "phone": "1234567890",
                "address": "New Address",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Profile updated successfully.")
        profile.refresh_from_db()
        self.assertEqual(profile.full_name, "Shubham V.")
        self.assertEqual(profile.phone, "1234567890")
        self.assertEqual(profile.address, "New Address")

    def test_profile_photo_delete_removes_photo(self):
        user = User.objects.create_user(username="shubham", password="StrongPass123!")
        self.client.force_login(user)

        profile = Profile.objects.create(
            user=user,
            full_name="Shubham Verma",
            phone="9876543210",
            address="Indore",
            profile_photo=SimpleUploadedFile("avatar.png", b"filecontent", content_type="image/png"),
        )

        response = self.client.post(
            reverse("profile_view"),
            {"action": "delete_photo"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Profile photo removed.")

        profile.refresh_from_db()
        self.assertFalse(profile.profile_photo)

    def test_logout_view_logs_user_out(self):
        user = User.objects.create_user(username="shubham", password="StrongPass123!")
        self.client.force_login(user)

        response = self.client.get(reverse("logout_view"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/profile/?mode=login")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_jwt_signup_returns_token_pair(self):
        response = self.client.post(
            reverse("jwt_signup_view"),
            data=json.dumps(
                {
                    "username": "jwtuser",
                    "full_name": "JWT User",
                    "email": "jwt@example.com",
                    "phone": "9876543210",
                    "address": "Indore",
                    "password": "StrongPass123!",
                    "confirm_password": "StrongPass123!",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("access", body["tokens"])
        self.assertIn("refresh", body["tokens"])
        self.assertTrue(User.objects.filter(username="jwtuser").exists())

    def test_jwt_login_and_me_view(self):
        user = User.objects.create_user(username="apiuser", password="StrongPass123!", email="api@example.com")
        Profile.objects.create(user=user, full_name="API User", phone="9999999999", address="API Street")

        login_response = self.client.post(
            reverse("jwt_login_view"),
            data=json.dumps({"username": "apiuser", "password": "StrongPass123!"}),
            content_type="application/json",
        )

        self.assertEqual(login_response.status_code, 200)
        access_token = login_response.json()["tokens"]["access"]

        me_response = self.client.get(
            reverse("jwt_me_view"),
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["user"]["username"], "apiuser")
        self.assertEqual(me_response.json()["user"]["full_name"], "API User")

    def test_jwt_refresh_returns_new_token_pair(self):
        user = User.objects.create_user(username="refreshuser", password="StrongPass123!")

        login_response = self.client.post(
            reverse("jwt_login_view"),
            data=json.dumps({"username": "refreshuser", "password": "StrongPass123!"}),
            content_type="application/json",
        )
        refresh_token = login_response.json()["tokens"]["refresh"]

        refresh_response = self.client.post(
            reverse("jwt_refresh_view"),
            data=json.dumps({"refresh": refresh_token}),
            content_type="application/json",
        )

        self.assertEqual(refresh_response.status_code, 200)
        self.assertTrue(refresh_response.json()["ok"])
        self.assertIn("access", refresh_response.json()["tokens"])
        self.assertIn("refresh", refresh_response.json()["tokens"])

    def test_jwt_me_rejects_missing_token(self):
        response = self.client.get(reverse("jwt_me_view"))

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["ok"])

    def test_healthcheck_view_returns_ok(self):
        response = self.client.get(reverse("healthcheck_view"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_unknown_url_uses_friendly_404_page(self):
        response = self.client.get("/does-not-exist/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)

    def test_menu_api_returns_paginated_results(self):
        for index in range(15):
            MenuItem.objects.create(
                name=f"Item {index}",
                category="Snacks",
                price="50",
                description="Snack",
                image="images/burger.jpg",
            )

        response = self.client.get(reverse("menu_api_view"), {"page_size": 5, "page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["page"], 2)
        self.assertEqual(response.json()["page_size"], 5)
        self.assertEqual(len(response.json()["results"]), 5)

    def test_my_orders_api_requires_bearer_token(self):
        response = self.client.get(reverse("my_orders_api_view"))

        self.assertEqual(response.status_code, 401)

    def test_my_orders_api_returns_paginated_orders(self):
        user = User.objects.create_user(username="apiorders", password="StrongPass123!")
        burger = MenuItem.objects.get(name="Burger")
        Profile.objects.create(user=user, full_name="API Orders", phone="9999999999", address="Order Street")
        for index in range(12):
            order = Order.objects.create(
                user=user,
                receiver_name=f"Receiver {index}",
                receiver_phone="9876543210",
                receiver_address="Street 1",
                subtotal="90.00",
                delivery_fee="35.00",
                tax_amount="4.50",
                grand_total="129.50",
            )
            OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")

        login_response = self.client.post(
            reverse("jwt_login_view"),
            data=json.dumps({"username": "apiorders", "password": "StrongPass123!"}),
            content_type="application/json",
        )
        access_token = login_response.json()["tokens"]["access"]

        response = self.client.get(
            reverse("my_orders_api_view"),
            {"page_size": 5, "page": 2},
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["page"], 2)
        self.assertEqual(len(response.json()["results"]), 5)

    def test_staff_can_update_order_status_via_api(self):
        cook = User.objects.create_user(username="cookapi", password="StrongPass123!", is_staff=True)
        order = Order.objects.create(
            receiver_name="Receiver",
            receiver_phone="9876543210",
            receiver_address="Street 1",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
        )

        login_response = self.client.post(
            reverse("jwt_login_view"),
            data=json.dumps({"username": "cookapi", "password": "StrongPass123!"}),
            content_type="application/json",
        )
        access_token = login_response.json()["tokens"]["access"]

        response = self.client.generic(
            "PATCH",
            reverse("order_status_api_view", args=[order.id]),
            data=json.dumps({"status": "accepted", "prep_minutes": 20}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "accepted")
        self.assertEqual(order.cook_prep_minutes, 20)

    @patch("MenuApp.views.create_razorpay_order")
    def test_razorpay_order_api_creates_payment_order(self, mock_create_razorpay_order):
        user = User.objects.create_user(username="payuser", password="StrongPass123!")
        burger = MenuItem.objects.get(name="Burger")
        order = Order.objects.create(
            user=user,
            receiver_name="Receiver",
            receiver_phone="9876543210",
            receiver_address="Street 1",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            payment_method="razorpay",
            payment_due_at=timezone.now() + timedelta(minutes=5),
        )
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")
        mock_create_razorpay_order.return_value = {"id": "order_razorpay_123", "amount": 12950}

        login_response = self.client.post(
            reverse("jwt_login_view"),
            data=json.dumps({"username": "payuser", "password": "StrongPass123!"}),
            content_type="application/json",
        )
        access_token = login_response.json()["tokens"]["access"]

        response = self.client.post(
            reverse("razorpay_order_api_view"),
            data=json.dumps({"order_id": order.id}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_provider, "razorpay")
        self.assertEqual(order.payment_reference, "order_razorpay_123")

    @patch("MenuApp.views.create_razorpay_order")
    def test_razorpay_order_session_view_creates_payment_order_for_logged_in_user(self, mock_create_razorpay_order):
        user = User.objects.create_user(username="sessionpayuser", password="StrongPass123!")
        burger = MenuItem.objects.get(name="Burger")
        order = Order.objects.create(
            user=user,
            receiver_name="Receiver",
            receiver_phone="9876543210",
            receiver_address="Street 1",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            payment_method="razorpay",
            payment_due_at=timezone.now() + timedelta(minutes=5),
        )
        OrderItem.objects.create(order=order, menu_item=burger, quantity=1, unit_price="90.00", line_total="90.00")
        mock_create_razorpay_order.return_value = {"id": "order_session_123", "amount": 12950, "currency": "INR"}

        self.client.login(username="sessionpayuser", password="StrongPass123!")
        response = self.client.post(
            reverse("razorpay_order_session_view"),
            data=json.dumps({"order_id": order.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        order.refresh_from_db()
        self.assertEqual(order.payment_provider, "razorpay")
        self.assertEqual(order.payment_reference, "order_session_123")

    def test_order_payment_action_switches_failed_pay_now_order_to_cod(self):
        user = User.objects.create_user(username="switchuser", password="StrongPass123!")
        order = Order.objects.create(
            user=user,
            receiver_name="Receiver",
            receiver_phone="9876543210",
            receiver_address="Street 1",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            payment_method="razorpay",
            payment_status="failed",
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("order_payment_action_view", args=[order.id]),
            {"action": "switch_to_cod"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_method, "cod")
        self.assertIsNotNone(order.confirmed_at)

    @override_settings(RAZORPAY_WEBHOOK_SECRET="test-webhook-secret")
    def test_razorpay_webhook_marks_order_paid_and_is_idempotent(self):
        user = User.objects.create_user(username="webhookuser", password="StrongPass123!")
        order = Order.objects.create(
            user=user,
            receiver_name="Receiver",
            receiver_phone="9876543210",
            receiver_address="Street 1",
            subtotal="90.00",
            delivery_fee="35.00",
            tax_amount="4.50",
            grand_total="129.50",
            payment_provider="razorpay",
            payment_reference="order_test_123",
            payment_status="pending",
        )

        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_123",
                        "order_id": "order_test_123",
                    }
                }
            },
        }
        raw_payload = json.dumps(payload).encode("utf-8")
        signature = hmac.new(
            b"test-webhook-secret",
            raw_payload,
            hashlib.sha256,
        ).hexdigest()

        response = self.client.post(
            reverse("razorpay_webhook_view_v1"),
            data=raw_payload,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=signature,
            HTTP_X_RAZORPAY_EVENT_ID="evt_test_1",
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(PaymentEvent.objects.count(), 1)

        second_response = self.client.post(
            reverse("razorpay_webhook_view_v1"),
            data=raw_payload,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=signature,
            HTTP_X_RAZORPAY_EVENT_ID="evt_test_1",
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_api_v1_menu_route_returns_data(self):
        response = self.client.get(reverse("menu_api_view_v1"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
