import logging
from contextlib import closing

import pytest

from app.config import PROJECT_ROOT
from app.policy.manual import (
    MANUAL_NAME,
    ManualSection,
    apply_corrections,
    parse_manual,
    split_sections,
)
from app.store.database import connect_read_only
from app.store.policy_sections import list_sections, read_section

MANUAL = PROJECT_ROOT / "data" / MANUAL_NAME


@pytest.fixture(scope="module")
def sections():
    if not MANUAL.exists():
        pytest.skip("data/ is not in the repository")
    return {section.number: section for section in parse_manual(MANUAL)}


def test_headings_are_accepted_only_in_sequence():
    lines = [
        "Capa",
        "1. Primeira",
        "1.1 Sub",
        "texto",
        "1. Saudação",
        "3. Fora de ordem",
        "1.3 Pulou",
        "13.709/2018 número",
        "2. Segunda",
        "fim",
    ]
    assert split_sections(lines) == [
        ManualSection("1", "Primeira", ""),
        ManualSection(
            "1.1", "Sub", "texto\n1. Saudação\n3. Fora de ordem\n1.3 Pulou\n13.709/2018 número"
        ),
        ManualSection("2", "Segunda", "fim"),
    ]


def test_correction_that_no_longer_matches_logs_a_warning(caplog):
    original = [ManualSection("7", "WhatsApp", "Canal novo")]
    with caplog.at_level(logging.WARNING):
        corrected = apply_corrections(original)
    assert corrected == original
    assert "no longer matches in section 7" in caplog.text


def test_finds_sections_1_to_10(sections):
    assert {str(n) for n in range(1, 11)} <= set(sections)
    assert sections["10"].title == "Disposições Finais"


def test_section_5_2_continues_across_the_page_break(sections):
    body = sections["5.2"].body
    assert "PAC (Correios)" in body
    assert "contato imediatamente com a loja" in body
    assert sections["5.3"].title == "Código de Rastreamento"


def test_list_in_7_2_stays_inside_7_2(sections):
    body = sections["7.2"].body
    assert "1. Saudação" in body
    assert "5. Fechamento" in body
    assert sections["7.3"].title == "Situações Especiais"


def test_page_furniture_is_gone(sections):
    text = "\n".join(section.body for section in sections.values())
    assert "Página " not in text
    assert "Manual de Políticas e Procedimentos" not in text
    assert "Última atualização" not in text


def test_contradictions_are_corrected(sections):
    text = "\n".join(section.body for section in sections.values())
    assert "3321-4500" not in text
    assert "(67) 3341-4444" in sections["7"].body
    assert "empóriodamusica" not in text
    assert "contato@emporiodamusica.com.br" in sections["1.2"].body
    assert "Parcela mínima de R$ 100,00" not in sections["3"].body
    assert "parcela mínima de R$ 100,00" in sections["3.1"].body
    assert "sem valor mínimo" not in text
    assert "parcela mínima de R$ 50,00" in sections["3.1"].body
    assert "lojas parceiras" not in text
    assert "mais de 300" not in text


def test_every_correction_matches_the_real_manual(caplog):
    if not MANUAL.exists():
        pytest.skip("data/ is not in the repository")
    with caplog.at_level(logging.WARNING):
        parse_manual(MANUAL)
    assert "no longer matches" not in caplog.text


def test_ingestion_saves_the_sections(real_db):
    with closing(connect_read_only(real_db)) as conn:
        index = list_sections(conn)
        assert index[0] == {"number": "1", "title": "Sobre a Empório da Música"}
        assert {"number": "10", "title": "Disposições Finais"} in index
        assert [row["number"] for row in read_section(conn, "5")] == ["5", "5.1", "5.2", "5.3"]
        assert [row["number"] for row in read_section(conn, "5.2")] == ["5.2"]
        assert [row["number"] for row in read_section(conn, "1")] == ["1", "1.1", "1.2"]
        assert read_section(conn, "99") == []
