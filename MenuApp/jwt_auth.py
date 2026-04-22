from django.contrib.auth.models import User
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


class JWTValidationError(Exception):
    pass


def build_token_pair(user):
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


def get_user_from_token(token, expected_type="access"):
    try:
        token_obj = AccessToken(token) if expected_type == "access" else RefreshToken(token)
    except (TokenError, InvalidToken) as exc:
        raise JWTValidationError("Invalid or expired token.") from exc

    token_type = token_obj.get("token_type")
    if token_type != expected_type:
        raise JWTValidationError("Invalid token type.")

    user_id = token_obj.get("user_id")
    if not user_id:
        raise JWTValidationError("Token payload is missing user information.")

    try:
        user = User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist as exc:
        raise JWTValidationError("User not found.") from exc

    return user, token_obj
