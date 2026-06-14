from __future__ import annotations

from pathlib import Path

from ..models import ClubConfig, TennisEvent


class ScraperError(RuntimeError):
    def __init__(self, message: str, artifact_path: str | None = None):
        super().__init__(message)
        self.artifact_path = artifact_path


class BaseScraper:
    def __init__(self, club: ClubConfig, data_dir: Path, headless: bool = True):
        self.club = club
        self.data_dir = data_dir
        self.headless = headless

    def fetch(self) -> list[TennisEvent]:
        raise NotImplementedError
