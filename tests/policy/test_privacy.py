import pytest

from app.policy.privacy import is_masked, mask_sensitive, pseudonymize, reveal


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Meu CPF é 529.982.247-25", "Meu CPF é [CPF omitido]"),
        ("cpf 52998224725", "cpf [CPF omitido]"),
        ("Cartão 4111 1111 1111 1111, validade 12/30", "Cartão [cartão omitido], validade 12/30"),
        ("4111-1111-1111-1111", "[cartão omitido]"),
    ],
)
def test_cpf_and_card_numbers_are_removed(text, expected):
    assert mask_sensitive(text) == expected
    assert pseudonymize(text, {}) == expected


def test_numbers_that_fail_their_check_digits_are_kept():
    # Not a valid CPF or card, so possibly a phone or a code.
    assert mask_sensitive("12345678900 e 4111 1111 1111 1112") == "12345678900 e 4111 1111 1111 1112"


@pytest.mark.parametrize(
    ("text", "mask", "real"),
    [
        ("E-mail: AnaCarol.Ferreira@coldmail.com", "a***@coldmail.com", "anacarol.ferreira@coldmail.com"),
        ("Fone (67) 99812-3456", "(67) *****-3456", "67998123456"),
        ("fone 67 3341-1234", "(67) ****-1234", "6733411234"),
        ("+55 67 99812-3456", "(67) *****-3456", "67998123456"),
        ("67998123456", "(67) *****-3456", "67998123456"),
        ("só 99812-3456", "*****-3456", "998123456"),
    ],
)
def test_contacts_become_masks_recorded_in_the_vault(text, mask, real):
    vault: dict[str, str] = {}
    masked = pseudonymize(text, vault)

    assert mask in masked
    assert real not in masked.lower()
    assert vault == {mask: real}


@pytest.mark.parametrize(
    "text",
    [
        "Liga no (67) 3341-4444 ou escreve para contato@emporiodamusica.com.br",
        "Rastreio BRAB1234567BR, pedido 8, protocolo EMP-000012",
        "Custa R$ 2.199,00 ou 12x de R$ 183,25, entrega em 2025-10-25",
        "Tenho até 1500 reais e moro no CEP 79002000",
    ],
)
def test_store_contacts_codes_prices_and_dates_are_kept(text):
    vault: dict[str, str] = {}
    assert pseudonymize(text, vault) == text
    assert vault == {}


def test_two_contacts_with_the_same_mask_get_different_masks():
    vault: dict[str, str] = {}
    masked = pseudonymize("ana@jmail.com e alice@jmail.com", vault)

    assert masked == "a***@jmail.com e al***@jmail.com"
    assert reveal("al***@jmail.com", vault) == "alice@jmail.com"


def test_the_same_contact_keeps_its_mask():
    vault: dict[str, str] = {}
    assert pseudonymize("ana@jmail.com", vault) == pseudonymize("Ana@Jmail.com", vault)
    assert len(vault) == 1


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("a***@coldmail.com", "anacarol.ferreira@coldmail.com"),
        ("(67) *****-3456", "67998123456"),
        ("67*****3456", "67998123456"),
        ("Ligar no (67) *****-3456 à tarde", "Ligar no 67998123456 à tarde"),
        (["a***@coldmail.com"], ["anacarol.ferreira@coldmail.com"]),
        (8, 8),
    ],
)
def test_reveal_restores_the_real_values(argument, expected):
    vault: dict[str, str] = {}
    pseudonymize("anacarol.ferreira@coldmail.com (67) 99812-3456", vault)
    assert reveal(argument, vault) == expected


def test_a_mask_without_a_real_value_stays_masked():
    assert reveal("a***@coldmail.com", {}) == "a***@coldmail.com"
    assert is_masked("a***@coldmail.com")
    assert is_masked(["(67) *****-3456"])
    assert not is_masked("anacarol.ferreira@coldmail.com")
