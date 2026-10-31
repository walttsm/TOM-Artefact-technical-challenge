from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import STORE_TIMEZONE

PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


def build_system_prompt(now: datetime | None = None) -> str:
    now = now or datetime.now(ZoneInfo(STORE_TIMEZONE))
    template = PROMPT_PATH.read_text(encoding="utf-8").strip()
    return f"{template}\n\nData e hora atuais: {now:%d/%m/%Y %H:%M}."
