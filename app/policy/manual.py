import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

MANUAL_NAME = "políticas_da_loja.pdf"

_PAGE_FURNITURE = (
    re.compile(r"^Empório da Música Manual de Políticas e Procedimentos$"),
    re.compile(r"^Página \d+$"),
    re.compile(r"^Empório da Música — Sua música começa aqui\.$"),
    re.compile(r"^Última atualização: .*$"),
)
_MAJOR_HEADING = re.compile(r"^(\d{1,2})\. (\S.*)$")
_MINOR_HEADING = re.compile(r"^(\d{1,2})\.(\d{1,2}) (\S.*)$")


@dataclass(frozen=True)
class ManualSection:
    number: str
    title: str
    body: str


@dataclass(frozen=True)
class Correction:
    section: str
    wrong: str
    right: str


# The reasons are in decisions.md. Spaces in `wrong` also match line breaks.
CORRECTIONS = (
    Correction("1", "abrange mais de 300 instrumentos", "abrange instrumentos"),
    Correction("1.2", "contato@empóriodamusica.com.br", "contato@emporiodamusica.com.br"),
    Correction("3", "Parcela mínima de R$ 100,00", "Parcela mínima conforme a seção 3.1"),
    Correction(
        "3.1",
        "sem juros, sem valor mínimo de parcela (exceto abaixo de R$ 50,00).",
        "sem juros, parcela mínima de R$ 50,00.",
    ),
    Correction("7", "(67) 3321-4500", "(67) 3341-4444"),
    Correction("7.1", " Sugerir lojas parceiras quando possível.", ""),
)


def _content_lines(page_text: str) -> list[str]:
    lines = (line.strip() for line in page_text.splitlines())
    return [
        line
        for line in lines
        if line and not any(pattern.match(line) for pattern in _PAGE_FURNITURE)
    ]


def split_sections(lines: list[str]) -> list[ManualSection]:
    """Accepts headings only in sequence, so the numbered list in 7.2 stays in its section."""
    sections: list[ManualSection] = []
    number: str | None = None
    title = ""
    body: list[str] = []
    major, minor = 0, 0

    def close() -> None:
        if number:
            sections.append(ManualSection(number, title, "\n".join(body)))

    for line in lines:
        minor_match = _MINOR_HEADING.match(line)
        major_match = _MAJOR_HEADING.match(line)
        if (
            minor_match
            and major > 0
            and int(minor_match[1]) == major
            and int(minor_match[2]) == minor + 1
        ):
            close()
            minor += 1
            number, title, body = f"{major}.{minor}", minor_match[3], []
        elif major_match and int(major_match[1]) == major + 1:
            close()
            major, minor = major + 1, 0
            number, title, body = str(major), major_match[2], []
        elif number:
            body.append(line)
    close()
    return sections


def apply_corrections(sections: list[ManualSection]) -> list[ManualSection]:
    bodies = {section.number: section.body for section in sections}
    for correction in CORRECTIONS:
        pattern = r"\s+".join(map(re.escape, correction.wrong.split(" ")))
        new_body, count = re.subn(
            pattern, correction.right, bodies.get(correction.section, ""), count=1
        )
        if count:
            bodies[correction.section] = new_body
        else:
            logger.warning(
                "Manual correction no longer matches in section %s: %s",
                correction.section,
                correction.wrong,
            )
    return [ManualSection(s.number, s.title, bodies[s.number]) for s in sections]


def parse_manual(pdf_path: Path) -> list[ManualSection]:
    with pdfplumber.open(pdf_path) as pdf:
        lines = [
            line for page in pdf.pages for line in _content_lines(page.extract_text() or "")
        ]
    return apply_corrections(split_sections(lines))
