from django.urls import path
from . import views

urlpatterns = [
    path('',views.home_view, name='home_view'),
    path('contact/',views.contact_view, name='contact_view'),
    path('services/',views.services_view, name='services_view'),
    path('menu/',views.menu_view, name='menu_view'),
    path('cart/',views.cart_view, name='cart_view'),
    path('order/',views.order_view, name='order_view'),
    path('orders/', views.my_orders_view, name='my_orders_view'),
    path('orders/<int:order_id>/', views.order_detail_view, name='order_detail_view'),
    path('profile/',views.profile_view, name='profile_view'),
    path('logout/',views.logout_view, name='logout_view'),
    path('cook/orders/', views.cook_orders_view, name='cook_orders_view'),
    path('cook/orders/history/', views.cook_order_history_view, name='cook_order_history_view'),
]
