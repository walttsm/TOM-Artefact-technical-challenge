"""Tools for the model. Each one calls store, applies policy and returns JSON.

The docstrings are written for the model, so they are in Portuguese.
"""

import json
import re
from collections.abc import Callable
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.policy.availability import product_situation, shows_price
from app.policy.identity import can_view_order, normalize_email, normalize_phone
from app.policy.pricing import ProductPrice, format_brl, installment_plan, price_product
from app.store.catalog import find_promotions, search_products
from app.store.database import connect_for_writing, connect_read_only
from app.store.handoffs import create_handoff
from app.store.loader import to_search_text
from app.store.orders import find_orders_by_contact, get_order_items, get_order_with_customer
from app.store.policy_sections import list_sections, read_section

ORDER_NOT_FOUND = (
    "Não foi possível localizar um pedido com os dados informados. "
    "Peça ao cliente para conferir o número do pedido, o e-mail e o telefone."
)
PRICE_HIDDEN = {
    "descontinuado": "Produto fora do catálogo: sem preço. Ofereça modelos equivalentes.",
    "em_breve": "Produto ainda não lançado: sem preço. Não prometa data de chegada.",
}
ORDER_STATUS = {
    "pending": "pendente",
    "confirmed": "confirmado",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}
PAYMENT_METHODS = {"pix": "PIX", "boleto": "boleto", "debit": "cartão de débito"}
MAX_RESULTS = 20


def _to_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


# Local models send loose arguments, such as "8" for 8 or a bare string for a list.
def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in ("", "null", "none") else text


def _as_int(value: Any) -> int | None:
    text = _as_text(value)
    if text is None:
        return None
    numbers = re.findall(r"\d+", re.sub(r"\.0+$", "", text))
    return int(numbers[0]) if len(numbers) == 1 else None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    items = value if isinstance(value, (list, tuple)) else [value]
    return [text for text in map(_as_text, items) if text]


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return to_search_text(value.strip()) in ("true", "sim", "1", "yes")
    return bool(value)


def _as_money(value: Any) -> Decimal | None:
    text = re.sub(r"[^\d,.\-]", "", _as_text(value) or "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _as_handoff_kind(value: Any) -> str | None:
    text = to_search_text(_as_text(value) or "").replace(" ", "_")
    if "reclama" in text:
        return "reclamacao"
    if "human" in text or "atendente" in text or "atendimento" in text:
        return "atendimento_humano"
    return None


def _price_fields(price: ProductPrice) -> dict:
    fields: dict[str, Any] = {"preco": format_brl(price.list_price)}
    if price.discount_percent:
        fields["preco_promocional"] = format_brl(price.final_price)
        fields["desconto"] = f"{price.discount_percent}%"
        fields["promocao"] = price.promotion_name
    else:
        fields["preco_pix"] = format_brl(price.pix_price)
    longest = installment_plan(price.final_price)[-1]
    fields["parcelamento_maximo"] = f"{longest.count}x de {format_brl(longest.value)} sem juros"
    return fields


def _product_view(row: dict) -> dict:
    situation = product_situation(row["status"], row["stock_quantity"])
    view = {
        "id": row["product_id"],
        "nome": row["name"],
        "categoria": row["category"],
        "situacao": situation,
        "descricao": row["description"],
        "especificacoes": json.loads(row["specs"]),
    }
    if shows_price(situation):
        view.update(
            _price_fields(
                price_product(row["price_brl"], row["discount_percent"], row["promotion_name"])
            )
        )
    else:
        view["aviso"] = PRICE_HIDDEN[situation]
    return view


def _promotion_view(row: dict) -> dict:
    situation = product_situation(row["status"], row["stock_quantity"])
    view: dict[str, Any] = {
        "promocao": row["promotion_name"],
        "produto": row["product_name"],
        "situacao_promocao": "ativa" if row["is_active"] else "expirada",
        "situacao_produto": situation,
    }
    if not shows_price(situation):
        view["aviso"] = PRICE_HIDDEN[situation]
    elif row["is_active"]:
        view.update(
            _price_fields(
                price_product(row["price_brl"], row["discount_percent"], row["promotion_name"])
            )
        )
    else:
        view["preco_atual"] = _price_fields(
            price_product(
                row["price_brl"], row["active_discount_percent"], row["active_promotion_name"]
            )
        )
    return view


def _payment_label(method: str) -> str:
    credit = re.fullmatch(r"credit_(\d+)x", method)
    return f"cartão de crédito em {credit[1]}x" if credit else PAYMENT_METHODS.get(method, method)


def _order_view(record: dict, items: list[dict]) -> dict:
    return {
        "numero": record["order_id"],
        "data": record["order_date"],
        "status": ORDER_STATUS.get(record["status"], record["status"]),
        "total": format_brl(Decimal(str(record["total_brl"]))),
        "pagamento": _payment_label(record["payment_method"]),
        "codigo_rastreio": record["tracking_code"],
        "previsao_entrega": record["estimated_delivery"],
        "observacoes": record["notes"],
        "itens": [{"produto": item["name"], "quantidade": item["quantity"]} for item in items],
    }


class StoreTools:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def all(self) -> list[Callable[..., str]]:
        return [
            self.consultar_politica,
            self.buscar_produtos,
            self.consultar_promocoes,
            self.consultar_pedido,
            self.calcular_parcelamento,
            self.registrar_encaminhamento,
        ]

    def consultar_politica(self, secao: str | None = None) -> str:
        """Lê uma seção do manual da loja, com as subseções dela.

        Args:
            secao: Número da seção, como "4" ou "5.2". Sem número, retorna o índice.
        """
        match = re.search(r"\d+(\.\d+)?", _as_text(secao) or "")
        with closing(connect_read_only(self._db_path)) as conn:
            sections = read_section(conn, match[0]) if match else []
            if sections:
                return _to_json({"secoes": sections})
            return _to_json(
                {"aviso": "Seção não encontrada. Use o índice.", "indice": list_sections(conn)}
            )

    def buscar_produtos(
        self,
        termos: list[str] | str | None = None,
        categoria: str | None = None,
        apenas_disponiveis: bool = False,
        preco_maximo: float | str | None = None,
        limite: int | str = 10,
    ) -> str:
        """Busca instrumentos no catálogo, com situação, preços e parcelamento.

        Args:
            termos: Palavras do produto, como ["takamine", "gd20"]. Não inclua preços.
            categoria: Nome da categoria, como "violões".
            apenas_disponiveis: Se verdadeiro, retorna só o que pode ser vendido agora.
            preco_maximo: Maior preço aceito pelo cliente, em reais.
            limite: Quantidade máxima de produtos retornados.
        """
        maximum = _as_money(preco_maximo)
        limit = min(max(_as_int(limite) or 10, 1), MAX_RESULTS)
        with closing(connect_read_only(self._db_path)) as conn:
            rows = search_products(
                conn,
                _as_list(termos),
                _as_text(categoria),
                _as_bool(apenas_disponiveis),
                limit=None,
            )
        if maximum is not None:
            rows = [row for row in rows if self._fits_budget(row, maximum)]
        return _to_json(
            {
                "total_encontrado": len(rows),
                "produtos": [_product_view(row) for row in rows[:limit]],
            }
        )

    @staticmethod
    def _fits_budget(row: dict, maximum: Decimal) -> bool:
        situation = product_situation(row["status"], row["stock_quantity"])
        final_price = price_product(row["price_brl"], row["discount_percent"]).final_price
        return shows_price(situation) and final_price <= maximum

    def consultar_promocoes(self, termo: str | None = None) -> str:
        """Lista as promoções ativas. Com um termo, mostra também as expiradas que combinam.

        Promoção expirada não dá desconto: informe que acabou e apresente o preço atual.

        Args:
            termo: Nome da promoção ou do produto, como "Black Friday" ou "Martin D-28".
        """
        term = _as_text(termo)
        with closing(connect_read_only(self._db_path)) as conn:
            rows = find_promotions(conn, term, include_expired=bool(term))
        # One entry per campaign and product: past percentages are never shown.
        unique: dict[tuple, dict] = {}
        for row in rows:
            unique.setdefault((row["product_id"], row["promotion_name"]), row)
        return _to_json({"promocoes": [_promotion_view(row) for row in unique.values()]})

    def consultar_pedido(
        self,
        numero_pedido: int | str | None = None,
        email: str | None = None,
        telefone: str | None = None,
    ) -> str:
        """Consulta um pedido do próprio cliente. Peça os dados ao cliente antes de chamar.

        Com o número do pedido, basta o e-mail ou o telefone. Sem o número, informe os dois.

        Args:
            numero_pedido: Número do pedido.
            email: E-mail do cliente.
            telefone: Telefone do cliente, com DDD.
        """
        has_number = _as_text(numero_pedido) is not None
        order_id = _as_int(numero_pedido)
        with closing(connect_read_only(self._db_path)) as conn:
            if not has_number:
                records = find_orders_by_contact(
                    conn, normalize_email(email), normalize_phone(telefone)
                )
            else:
                record = get_order_with_customer(conn, order_id) if order_id else None
                records = [record] if record else []
            allowed = [
                record
                for record in records
                if can_view_order(record, email, telefone, by_order_number=has_number)
            ]
            views = [
                _order_view(record, get_order_items(conn, record["order_id"]))
                for record in allowed
            ]
        return _to_json({"pedidos": views} if views else {"erro": ORDER_NOT_FOUND})

    def calcular_parcelamento(self, valor: float | str) -> str:
        """Calcula as opções de parcelamento sem juros no cartão de crédito.

        Args:
            valor: Valor total da compra, em reais.
        """
        amount = _as_money(valor)
        if amount is None or amount <= 0:
            return _to_json({"erro": "Informe um valor maior que zero."})
        options = installment_plan(amount)
        return _to_json(
            {
                "valor": format_brl(amount),
                "parcelamento_maximo": options[-1].count,
                "opcoes": [
                    {"parcelas": option.count, "valor_parcela": format_brl(option.value)}
                    for option in options
                ],
            }
        )

    def registrar_encaminhamento(self, tipo: str, resumo: str, contato: str) -> str:
        """Registra uma reclamação ou um pedido para falar com a equipe e gera um protocolo.

        Só diga que registrou depois de receber o protocolo desta ferramenta.

        Args:
            tipo: "reclamacao" ou "atendimento_humano".
            resumo: Resumo do caso, nas palavras do cliente.
            contato: Telefone ou e-mail para a equipe retornar.
        """
        kind = _as_handoff_kind(tipo)
        summary, contact = _as_text(resumo), _as_text(contato)
        if not kind:
            return _to_json({"erro": 'Use o tipo "reclamacao" ou "atendimento_humano".'})
        if not summary or not contact:
            return _to_json({"erro": "Peça ao cliente o resumo do caso e um contato."})
        with closing(connect_for_writing(self._db_path)) as conn:
            handoff_id = create_handoff(conn, kind, summary, contact)
        result = {"registrado": True, "protocolo": f"EMP-{handoff_id:06d}"}
        if kind == "reclamacao":
            result["prazo_retorno"] = "24 horas úteis"
        return _to_json(result)
