from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import STORE_TIMEZONE

PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"
SUMMARY_PROMPT_PATH = PROMPT_PATH.with_name("summary_prompt.md")

# Spelled out because strftime's %A depends on the machine's locale.
WEEKDAYS = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]


def build_system_prompt(now: datetime | None = None) -> str:
    now = now or datetime.now(ZoneInfo(STORE_TIMEZONE))
    template = PROMPT_PATH.read_text(encoding="utf-8").strip()
    return f"{template}\n\nData e hora atuais: {WEEKDAYS[now.weekday()]}, {now:%d/%m/%Y %H:%M}."


def build_summary_prompt() -> str:
    return SUMMARY_PROMPT_PATH.read_text(encoding="utf-8").strip()
