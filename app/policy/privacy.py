"""Masks for the personal data that customers type into a conversation.

CPF and card numbers are removed, since no tool needs them. Emails and phones become
readable masks, and `pseudonymize` records each mask with its real value in a vault,
so `reveal` can restore the value when a tool runs.
"""

import re
from collections.abc import Iterator

CPF_MASK = "[CPF omitido]"
CARD_MASK = "[cartão omitido]"

# The store's own contacts, as in the prompt. Customers may repeat them.
STORE_EMAILS = {"contato@emporiodamusica.com.br"}
STORE_PHONES = {"6733414444"}

# A number must stand alone: not inside a word, a code or an amount such as 2.199,00.
ALONE_BEFORE = r"(?<![\w*])(?<!\d[.,])"
ALONE_AFTER = r"(?!\w|[.,]\d)"
CARD = re.compile(rf"{ALONE_BEFORE}\d(?:[ -]?\d){{12,18}}{ALONE_AFTER}")
CPF = re.compile(rf"{ALONE_BEFORE}(?:\d{{3}}\.\d{{3}}\.\d{{3}}-\d{{2}}|\d{{11}}){ALONE_AFTER}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(
    rf"{ALONE_BEFORE}(?:\+?55[ -]?)?(?:\(\d{{2}}\) ?|\d{{2}}[ -]?)?\d{{4,5}}[ -]?\d{{4}}{ALONE_AFTER}"
)
MASKED = "***"


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _passes_luhn(digits: str) -> bool:
    total = 0
    for position, digit in enumerate(int(d) for d in reversed(digits)):
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _is_cpf(digits: str) -> bool:
    if len(set(digits)) == 1:
        return False
    for length in (9, 10):
        total = sum(int(d) * weight for d, weight in zip(digits, range(length + 1, 1, -1)))
        if (total * 10 % 11) % 10 != int(digits[length]):
            return False
    return True


def _split_phone(text: str) -> tuple[str, str] | None:
    """Area code and local number, or None if the text isn't a phone number."""
    digits = _digits(text)
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    if len(digits) in (10, 11):
        return digits[:2], digits[2:]
    # Without an area code, only the written form "99812-3456" is taken as a phone.
    if len(digits) in (8, 9) and "-" in text:
        return "", digits
    return None


def _email_masks(email: str) -> Iterator[str]:
    local, domain = email.split("@", 1)
    for shown in range(1, len(local)):
        yield f"{local[:shown]}{MASKED}@{domain}"


def _phone_masks(area: str, number: str) -> Iterator[str]:
    prefix = f"({area}) " if area else ""
    for shown in range(4, len(number)):
        hidden = "*" * (len(number) - shown)
        yield f"{prefix}{hidden}-{number[-shown:]}"


def _record(masks: Iterator[str], real: str, vault: dict[str, str]) -> str | None:
    # Two values could share a mask, so a later one shows a little more of itself.
    for mask in masks:
        if vault.setdefault(mask, real) == real:
            return mask
    return None


def mask_sensitive(text: str) -> str:
    """Remove CPF and card numbers, which nothing in the store needs."""
    text = CARD.sub(lambda m: CARD_MASK if _passes_luhn(_digits(m[0])) else m[0], text)
    return CPF.sub(lambda m: CPF_MASK if _is_cpf(_digits(m[0])) else m[0], text)


def pseudonymize(text: str, vault: dict[str, str]) -> str:
    """Mask CPF, cards, emails and phones, recording each email and phone in the vault."""

    def email(match: re.Match) -> str:
        real = match[0].lower()
        if real in STORE_EMAILS:
            return match[0]
        return _record(_email_masks(real), real, vault) or match[0]

    def phone(match: re.Match) -> str:
        parts = _split_phone(match[0])
        if not parts or "".join(parts) in STORE_PHONES:
            return match[0]
        return _record(_phone_masks(*parts), "".join(parts), vault) or match[0]

    text = mask_sensitive(text)
    text = EMAIL.sub(email, text)
    return PHONE.sub(phone, text)


def _compact(text: str) -> str:
    return re.sub(r"[\s()+-]", "", text).lower()


def reveal(value: object, vault: dict[str, str]) -> object:
    """Put the real values back where the model wrote a mask."""
    if isinstance(value, list):
        return [reveal(item, vault) for item in value]
    if not isinstance(value, str) or MASKED not in value:
        return value
    # Local models may drop the spaces or brackets of a phone mask.
    for mask, real in vault.items():
        if _compact(value) == _compact(mask):
            return real
    for mask in sorted(vault, key=len, reverse=True):
        value = value.replace(mask, vault[mask])
    return value


def is_masked(value: object) -> bool:
    """Whether a mask is still there, e.g. because the real value is no longer held."""
    if isinstance(value, list):
        return any(is_masked(item) for item in value)
    return isinstance(value, str) and MASKED in value
