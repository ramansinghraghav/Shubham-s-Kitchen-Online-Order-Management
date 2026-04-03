def cart_count(request):
    cart = request.session.get("cart", {})
    profile = None
    if request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
    return {
        "nav_cart_count": sum(cart.values()),
        "nav_profile": profile,
    }
