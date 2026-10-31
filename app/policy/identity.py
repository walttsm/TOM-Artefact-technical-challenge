import re


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def normalize_phone(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    # Numbers stored in the data have an area code and no country code.
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits[2:]
    return digits


def can_view_order(
    record: dict, email: str | None, phone: str | None, by_order_number: bool = True
) -> bool:
    """An order number needs one matching contact; without a number, both are required.

    A shared email proves nothing on its own, so it never counts as the one contact.
    """
    given_email, given_phone = normalize_email(email), normalize_phone(phone)
    email_matches = bool(given_email) and given_email == record["email"]
    phone_matches = bool(given_phone) and given_phone == record["phone"]
    if not by_order_number:
        return email_matches and phone_matches
    if record["email_is_shared"]:
        return phone_matches
    return email_matches or phone_matches
