import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

STORE_TIMEZONE = "America/Campo_Grande"


@dataclass(frozen=True)
class Settings:
    ollama_host: str
    ollama_model: str
    # Always sent: without it Ollama may truncate the prompt silently.
    ollama_num_ctx: int
    ollama_timeout: float
    data_dir: Path = PROJECT_ROOT / "data"
    db_path: Path = PROJECT_ROOT / "storage" / "emporio.db"


def get_settings() -> Settings:
    return Settings(
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:12b"),
        ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "16384")),
        ollama_timeout=float(os.getenv("OLLAMA_TIMEOUT", "300")),
        data_dir=Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data")),
        db_path=Path(os.getenv("DB_PATH", PROJECT_ROOT / "storage" / "emporio.db")),
    )
