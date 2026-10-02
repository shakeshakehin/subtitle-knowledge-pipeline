from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    project_root: Path
    note_base_url: str
    note_model: str
    note_api_key: str
    report_provider: str
    report_model: str
    report_api_key: str
    obsidian_root: Path
    credential_path: Path

    @classmethod
    def load(cls, project_root: Path) -> "Settings":
        project_root = project_root.resolve()
        load_dotenv(project_root / ".env")
        default_obsidian = Path.home() / "Documents" / "Obsidian Vault" / "07-AI学习" / "视频融合成果"
        return cls(
            project_root=project_root,
            note_base_url=os.getenv("NOTE_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            note_model=os.getenv("NOTE_MODEL", "deepseek-flash"),
            note_api_key=os.getenv("NOTE_API_KEY", ""),
            report_provider=os.getenv("REPORT_PROVIDER", "deepseek"),
            report_model=os.getenv("REPORT_MODEL", "deepseek-flash"),
            report_api_key=os.getenv("REPORT_API_KEY", ""),
            obsidian_root=Path(os.getenv("OBSIDIAN_ROOT", str(default_obsidian))).expanduser(),
            credential_path=Path(
                os.getenv("BILIBILI_CREDENTIAL", str(Path.home() / ".bilibili-cli" / "credential.json"))
            ).expanduser(),
        )
