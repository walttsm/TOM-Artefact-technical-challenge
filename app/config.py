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
    checkpoints_path: Path = PROJECT_ROOT / "storage" / "checkpoints.db"
    # A session idle for longer than this is deleted, with everything it holds.
    session_ttl_days: float = 30
    # Used when the provider is "openai": OpenAI itself, or a compatible server.
    openai_model: str = ""
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_timeout: float = 60
    openai_context_tokens: int = 16384


def get_settings() -> Settings:
    return Settings(
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:12b"),
        ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "16384")),
        ollama_timeout=float(os.getenv("OLLAMA_TIMEOUT", "300")),
        data_dir=Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data")),
        db_path=Path(os.getenv("DB_PATH", PROJECT_ROOT / "storage" / "emporio.db")),
        checkpoints_path=Path(
            os.getenv("CHECKPOINTS_PATH", PROJECT_ROOT / "storage" / "checkpoints.db")
        ),
        session_ttl_days=float(os.getenv("SESSION_TTL_DAYS", "30")),
    )
