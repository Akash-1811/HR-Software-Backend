import re

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from Attenova.api_utils import parse_json_request
from marketing.email_demo import send_book_demo_notification


_EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$",
)


def _trim(s: str, max_len: int) -> str:
    return (s or "").strip()[:max_len]


@csrf_exempt
@require_http_methods(["POST"])
def book_demo(request):
    """
    POST /api/contact/book-demo/

    Public endpoint (no auth). Body JSON:
      name, company_email, company_name, contact_number, message (optional)
    Sends a branded notification to DEMO_BOOKING_INBOX.
    """
    data, err = parse_json_request(request)
    if err:
        return err

    name = _trim(str(data.get("name", "")), 120)
    company_email = _trim(str(data.get("company_email", "")), 254)
    company_name = _trim(str(data.get("company_name", "")), 200)
    contact_number = _trim(str(data.get("contact_number", "")), 48)
    message = _trim(str(data.get("message", "")), 6000)

    if not name or not company_email or not company_name or not contact_number:
        return JsonResponse(
            {"error": "name, company_email, company_name, and contact_number are required"},
            status=400,
        )
    if not _EMAIL_PATTERN.match(company_email):
        return JsonResponse({"error": "Invalid company email"}, status=400)

    try:
        send_book_demo_notification(
            name=name,
            company_email=company_email,
            company_name=company_name,
            contact_number=contact_number,
            message=message,
        )
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=503)
    except OSError:
        return JsonResponse(
            {"error": "Email could not be sent. Please try again later."},
            status=503,
        )
    except Exception:
        return JsonResponse(
            {"error": "Something went wrong while sending your request."},
            status=503,
        )

    return JsonResponse({"success": True})
