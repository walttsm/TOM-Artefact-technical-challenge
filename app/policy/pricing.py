from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

PIX_DISCOUNT = Decimal("0.05")
MAX_INSTALLMENTS = 12
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Installment:
    count: int
    value: Decimal


@dataclass(frozen=True)
class ProductPrice:
    list_price: Decimal
    final_price: Decimal
    discount_percent: int | None
    promotion_name: str | None
    # None when a promotion applies: the two discounts don't combine.
    pix_price: Decimal | None


def to_money(value: Decimal | float | int | str) -> Decimal:
    # Through str so that a stored 599.9 stays 599.9, not its binary approximation.
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def format_brl(value: Decimal) -> str:
    text = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {text}"


def _minimum_installment(count: int) -> Decimal:
    if count <= 3:
        return Decimal("50")
    if count <= 6:
        return Decimal("80")
    return Decimal("100")


def is_installment_allowed(total: Decimal | float, count: int) -> bool:
    if not 1 <= count <= MAX_INSTALLMENTS:
        return False
    # One payment is always possible, whatever the value.
    return count == 1 or to_money(total) / count >= _minimum_installment(count)


def installment_plan(total: Decimal | float) -> list[Installment]:
    """Every allowed option, from 1x up. The last one is the longest plan."""
    amount = to_money(total)
    if amount <= 0:
        raise ValueError("The total must be positive.")
    return [
        Installment(count, (amount / count).quantize(CENT, rounding=ROUND_HALF_UP))
        for count in range(1, MAX_INSTALLMENTS + 1)
        if is_installment_allowed(amount, count)
    ]


def price_product(
    price: Decimal | float, discount: int | None = None, promo_name: str | None = None
) -> ProductPrice:
    list_price = to_money(price)
    if discount:
        final_price = to_money(list_price * (100 - discount) / 100)
        return ProductPrice(list_price, final_price, discount, promo_name, None)
    pix_price = to_money(list_price * (1 - PIX_DISCOUNT))
    return ProductPrice(list_price, list_price, None, None, pix_price)
