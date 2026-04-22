import base64
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings


class RazorpayConfigurationError(Exception):
    pass


class RazorpayAPIError(Exception):
    pass


def create_razorpay_order(*, amount_rupees, receipt, notes=None):
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise RazorpayConfigurationError("Razorpay keys are not configured.")

    amount_paise = int(round(float(amount_rupees) * 100))
    payload = json.dumps(
        {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": notes or {},
        }
    ).encode("utf-8")
    auth_header = base64.b64encode(
        f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode("utf-8")
    ).decode("ascii")
    request = Request(
        "https://api.razorpay.com/v1/orders",
        data=payload,
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except (HTTPError, URLError, ValueError) as exc:
        raise RazorpayAPIError("Razorpay order creation failed.") from exc
