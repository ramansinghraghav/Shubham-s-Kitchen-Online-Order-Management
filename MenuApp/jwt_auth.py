import base64
import hashlib
import hmac
import json
import time

from django.conf import settings
from django.contrib.auth.models import User


class JWTValidationError(Exception):
    pass


def _b64url_encode(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")


def _b64url_decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _json_dumps(payload):
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sign(message):
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256).digest()


def _password_fingerprint(user):
    return hashlib.sha256(user.password.encode("utf-8")).hexdigest()[:16]


def encode_token(payload):
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _b64url_encode(_json_dumps(header))
    encoded_payload = _b64url_encode(_json_dumps(payload))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = _b64url_encode(_sign(signing_input))
    return f"{encoded_header}.{encoded_payload}.{signature}"


def decode_token(token):
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
    except ValueError as exc:
        raise JWTValidationError("Invalid token format.") from exc

    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected_signature = _b64url_encode(_sign(signing_input))
    if not hmac.compare_digest(encoded_signature, expected_signature):
        raise JWTValidationError("Invalid token signature.")

    try:
        payload = json.loads(_b64url_decode(encoded_payload))
    except (json.JSONDecodeError, ValueError) as exc:
        raise JWTValidationError("Invalid token payload.") from exc

    if int(payload.get("exp", 0)) <= int(time.time()):
        raise JWTValidationError("Token has expired.")

    return payload


def build_token_pair(user):
    now = int(time.time())
    access_payload = {
        "sub": str(user.pk),
        "username": user.username,
        "type": "access",
        "is_staff": user.is_staff,
        "pwd": _password_fingerprint(user),
        "iat": now,
        "exp": now + (settings.JWT_ACCESS_TOKEN_MINUTES * 60),
    }
    refresh_payload = {
        "sub": str(user.pk),
        "type": "refresh",
        "pwd": _password_fingerprint(user),
        "iat": now,
        "exp": now + (settings.JWT_REFRESH_TOKEN_DAYS * 24 * 60 * 60),
    }
    return {
        "access": encode_token(access_payload),
        "refresh": encode_token(refresh_payload),
    }


def get_user_from_token(token, expected_type="access"):
    payload = decode_token(token)
    if payload.get("type") != expected_type:
        raise JWTValidationError("Invalid token type.")

    try:
        user = User.objects.get(pk=payload.get("sub"), is_active=True)
    except User.DoesNotExist as exc:
        raise JWTValidationError("User not found.") from exc

    if payload.get("pwd") != _password_fingerprint(user):
        raise JWTValidationError("Token is no longer valid.")

    return user, payload
