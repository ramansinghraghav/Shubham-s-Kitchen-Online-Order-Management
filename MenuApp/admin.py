from django.contrib import admin
from .models import MenuItem, Order, OrderItem, Profile

admin.site.site_header = "SHUBHAM's KITCHEN Admin Portal"
admin.site.site_title = "SHUBHAM's KITCHEN Admin Portal"
admin.site.index_title = "Welcome to SHUBHAM's KITCHEN Admin   Portal"

@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price")
    search_fields = ("name", "category", "description")
    list_filter = ("category",)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "full_name", "phone")
    search_fields = ("user__username", "full_name", "phone")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "receiver_name", "status", "payment_status", "grand_total", "created_at")
    search_fields = ("receiver_name", "receiver_phone", "payment_reference", "user__username")
    list_filter = ("status", "payment_status", "payment_provider", "created_at")
    inlines = [OrderItemInline]
