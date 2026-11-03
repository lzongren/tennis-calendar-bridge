"""Transport-neutral browser observations, not an API for controlling a browser.

Only a trusted reader may construct these. Never accept observations or their
capture timestamps from model tool arguments. Authentication stays in the reader.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BrowserDocument:
    url: str
    title: str
    html: str
    captured_at: datetime
