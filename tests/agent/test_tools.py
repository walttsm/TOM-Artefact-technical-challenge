import json
from contextlib import closing

import pytest

from app.agent.tools import ORDER_NOT_FOUND, StoreTools
from app.config import PROJECT_ROOT
from app.ingest import ingest
from app.store.database import connect_read_only

REAL_DATA = PROJECT_ROOT / "data"
ANA = "anacarol.ferreira@coldmail.com"


@pytest.fixture
def tools(real_db):
    return StoreTools(real_db)


@pytest.fixture
def writable_tools(real_db, tmp_path):
    db_path = tmp_path / "test.db"
    ingest(REAL_DATA, db_path)
    return StoreTools(db_path), db_path


def call(tool, *args, **kwargs):
    return json.loads(tool(*args, **kwargs))


def one_product(tools, *terms):
    products = call(tools.buscar_produtos, list(terms))["produtos"]
    assert len(products) == 1
    return products[0]


def test_every_tool_has_a_docstring_for_the_model(tools):
    assert len(tools.all()) == 6
    assert all(tool.__doc__ and "Args:" in tool.__doc__ for tool in tools.all())


def test_policy_section_with_subsections(tools):
    sections = call(tools.consultar_politica, "5")["secoes"]
    assert [section["number"] for section in sections] == ["5", "5.1", "5.2", "5.3"]


@pytest.mark.parametrize("section", [None, "", "99", "trocas"])
def test_policy_without_a_valid_section_returns_the_index(tools, section):
    index = call(tools.consultar_politica, section)["indice"]
    assert {"number": "4", "title": "Política de Trocas e Devoluções"} in index


def test_policy_accepts_loose_section_values(tools):
    assert call(tools.consultar_politica, "seção 4.2")["secoes"][0]["number"] == "4.2"
    assert call(tools.consultar_politica, 8)["secoes"][0]["number"] == "8"


def test_product_with_all_prices(tools):
    product = one_product(tools, "Takamine GD20")
    assert product["situacao"] == "disponivel"
    assert product["preco"] == "R$ 2.199,00"
    assert product["preco_pix"] == "R$ 2.089,05"
    assert product["parcelamento_maximo"] == "12x de R$ 183,25 sem juros"


def test_product_on_promotion_has_no_pix_price(tools):
    product = one_product(tools, "Crafter HT-100")
    assert product["preco"] == "R$ 2.399,00"
    assert product["preco_promocional"] == "R$ 1.967,18"
    assert product["desconto"] == "18%"
    assert "preco_pix" not in product
    assert product["parcelamento_maximo"].startswith("12x de R$ 163,93")


def test_sold_out_product_keeps_its_price(tools):
    product = one_product(tools, "Giannini GF-3D")
    assert product["situacao"] == "esgotado"
    assert product["preco"] == "R$ 799,90"


@pytest.mark.parametrize(
    ("term", "situation"),
    [("Shelby SN-7C", "descontinuado"), ("Ibanez Bass 2X", "descontinuado"), ("KA-ABP", "em_breve")],
)
def test_products_not_on_sale_have_no_price(tools, term, situation):
    product = one_product(tools, term)
    assert product["situacao"] == situation
    assert not {"preco", "preco_pix", "parcelamento_maximo"} & set(product)
    assert product["aviso"]


def test_budget_uses_the_final_price_and_skips_hidden_prices(tools):
    # List price is R$ 549,00; 20% off gives R$ 439,20.
    assert call(tools.buscar_produtos, ["Ohana CK-20"], preco_maximo=450)["total_encontrado"] == 1
    assert call(tools.buscar_produtos, ["Ohana CK-20"], preco_maximo=400)["total_encontrado"] == 0
    assert call(tools.buscar_produtos, ["Shelby SN-7C"], preco_maximo=99999)["produtos"] == []


def test_available_violoes_under_a_budget(tools):
    result = call(
        tools.buscar_produtos, categoria="violões", apenas_disponiveis=True, preco_maximo=1000
    )
    assert result["total_encontrado"] > len(result["produtos"]) == 10
    assert {product["situacao"] for product in result["produtos"]} == {"disponivel"}
    assert {product["categoria"] for product in result["produtos"]} == {"Violões"}


def test_active_promotions(tools):
    promotions = call(tools.consultar_promocoes)["promocoes"]
    assert len(promotions) == 4
    assert {promotion["situacao_promocao"] for promotion in promotions} == {"ativa"}
    assert {promotion["produto"] for promotion in promotions} >= {"Crafter HT-100 Folk Aço Natural"}
    assert all("preco_promocional" in promotion for promotion in promotions)


def test_black_friday_is_expired_and_shows_no_old_discount(tools):
    result = call(tools.consultar_promocoes, "Black Friday")["promocoes"]
    assert len(result) == 3
    assert {promotion["situacao_promocao"] for promotion in result} == {"expirada"}
    assert all("preco_promocional" not in promotion for promotion in result)
    assert "%" not in json.dumps(result, ensure_ascii=False)
    by_product = {promotion["situacao_produto"]: promotion for promotion in result}
    assert "preco_pix" in by_product["disponivel"]["preco_atual"]
    assert "preco_atual" not in by_product["descontinuado"]


def test_repeated_campaign_appears_once_per_product(tools):
    result = call(tools.consultar_promocoes, "Martin D-28 Dreadnought")["promocoes"]
    assert [promotion["promocao"] for promotion in result] == ["Promoção de Páscoa"]


def test_order_with_a_matching_contact(tools):
    order = call(tools.consultar_pedido, 8, email=ANA)["pedidos"][0]
    assert order["numero"] == 8
    assert order["status"] == "enviado"
    assert order["total"] == "R$ 349,90"
    assert order["codigo_rastreio"] == "BRJL5544332BR"
    assert order["pagamento"] == "cartão de débito"
    assert order["itens"] == [{"produto": "Kala KA-C Concert Mogno", "quantidade": 1}]


def test_order_keeps_the_recorded_total_and_shows_no_item_prices(tools):
    order = call(tools.consultar_pedido, 3, telefone="(67) 99678-9012")["pedidos"][0]
    assert order["total"] == "R$ 3.450,00"
    assert order["pagamento"] == "cartão de crédito em 12x"
    assert set(order["itens"][0]) == {"produto", "quantidade"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"numero_pedido": 8, "email": "lucas.mendes@jmail.com"},
        {"numero_pedido": 8},
        {"numero_pedido": 999, "email": ANA},
        {"numero_pedido": "abc", "email": ANA},
        {"email": ANA},
        {"telefone": "67996547821"},
        {"email": ANA, "telefone": "67900000000"},
        {},
        {"numero_pedido": 10, "email": "bruno.martins@jmail.com"},
        {"numero_pedido": 10, "email": "bruno.martins@jmail.com", "telefone": "67994563456"},
    ],
)
def test_every_order_failure_returns_the_same_message(tools, kwargs):
    assert call(tools.consultar_pedido, **kwargs) == {"erro": ORDER_NOT_FOUND}


def test_order_10_opens_with_the_phone_of_its_customer(tools):
    result = call(tools.consultar_pedido, 10, "bruno.martins@jmail.com", "(67) 98543-2109")
    assert result["pedidos"][0]["numero"] == 10


def test_orders_by_contact_needs_both(tools):
    result = call(tools.consultar_pedido, email="pedro.oliveira@jmail.com", telefone="67984321098")
    assert [order["numero"] for order in result["pedidos"]] == [1, 19]


def test_customer_data_never_appears_in_an_order(tools):
    text = tools.consultar_pedido(8, email=ANA)
    assert ANA not in text
    assert "Ana Carolina" not in text
    assert "99654" not in text


def test_installments(tools):
    result = call(tools.calcular_parcelamento, 2199)
    assert result["parcelamento_maximo"] == 12
    assert result["opcoes"][-1] == {"parcelas": 12, "valor_parcela": "R$ 183,25"}


@pytest.mark.parametrize("value", [0, -5, "abc", None])
def test_installments_reject_invalid_values(tools, value):
    assert "erro" in call(tools.calcular_parcelamento, value)


def test_handoff_returns_a_protocol(writable_tools):
    tools, db_path = writable_tools
    result = call(tools.registrar_encaminhamento, "reclamacao", "Pedido atrasou", "ana@jmail.com")
    assert result == {
        "registrado": True,
        "protocolo": "EMP-000001",
        "prazo_retorno": "24 horas úteis",
    }
    with closing(connect_read_only(db_path)) as conn:
        row = conn.execute("SELECT kind, summary, contact FROM handoffs").fetchone()
    assert tuple(row) == ("reclamacao", "Pedido atrasou", "ana@jmail.com")


def test_human_handoff_promises_no_deadline(writable_tools):
    tools, _ = writable_tools
    result = call(tools.registrar_encaminhamento, "Atendimento Humano", "Quer um atendente", "67")
    assert result["protocolo"] == "EMP-000001"
    assert "prazo_retorno" not in result


@pytest.mark.parametrize(
    ("kind", "summary", "contact"),
    [("outro", "x", "y"), ("reclamacao", "", "y"), ("reclamacao", "x", None)],
)
def test_incomplete_handoff_records_nothing(writable_tools, kind, summary, contact):
    tools, db_path = writable_tools
    assert "erro" in call(tools.registrar_encaminhamento, kind, summary, contact)
    with closing(connect_read_only(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0


def test_loose_arguments(tools):
    assert call(tools.consultar_pedido, "8", email=ANA)["pedidos"][0]["numero"] == 8
    assert call(tools.consultar_pedido, 8.0, email=ANA)["pedidos"][0]["numero"] == 8
    assert call(tools.consultar_pedido, "pedido 8", email=ANA)["pedidos"][0]["numero"] == 8
    assert call(tools.buscar_produtos, "Takamine GD20")["total_encontrado"] == 1
    assert call(tools.buscar_produtos, '["Takamine", "GD20"]')["total_encontrado"] == 1
    assert call(tools.buscar_produtos, "Giannini GF-3D", apenas_disponiveis="false")["produtos"]
    assert not call(tools.buscar_produtos, "Giannini GF-3D", apenas_disponiveis="true")["produtos"]
    assert len(call(tools.buscar_produtos, "violao", limite="3")["produtos"]) == 3
    assert call(tools.buscar_produtos, "violao", categoria="null")["total_encontrado"] == 36
    assert call(tools.calcular_parcelamento, "R$ 2.199,00")["parcelamento_maximo"] == 12
    assert call(tools.calcular_parcelamento, "2199.00")["parcelamento_maximo"] == 12
    assert call(tools.calcular_parcelamento, "2.199")["parcelamento_maximo"] == 12
