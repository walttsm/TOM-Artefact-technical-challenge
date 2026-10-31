def product_situation(status: str, stock: int) -> str:
    """Status wins over stock: units on hand don't make a withdrawn product sellable."""
    if status == "discontinued":
        return "descontinuado"
    if status == "coming_soon":
        return "em_breve"
    if status == "active":
        return "disponivel" if stock > 0 else "esgotado"
    raise ValueError(f"Unknown product status: {status}")


def shows_price(situation: str) -> bool:
    return situation in ("disponivel", "esgotado")
