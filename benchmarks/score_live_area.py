"""Score a saved blind run against an independently observed, public occurrence."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Reference(BaseModel):
    source_url: str
    starts_at: datetime
    ends_at: datetime
    price_usd: Decimal = Field(ge=0)
    spots_left: int = Field(gt=0, strict=True)


def score(report: dict[str, Any], reference: Reference) -> dict[str, bool]:
    matches = [
        c
        for c in report["courses"]
        if c["source_url"] == reference.source_url
        and c.get("evidence_ids")
        and any(
            datetime.fromisoformat(s["starts_at"]) == reference.starts_at
            and datetime.fromisoformat(s["ends_at"]) == reference.ends_at
            for s in c["sessions"]
        )
    ]
    course = matches[0] if len(matches) == 1 else {}
    price = re.fullmatch(r"\$(\d+(?:\.\d{2})?)(?: USD)?", course.get("price", ""))
    spots = course.get("spots_left")
    return {
        "occurrence_found": bool(course),
        "price_verified": price is not None and Decimal(price[1]) == reference.price_usd,
        "remaining_verified": len(course.get("sessions", [])) == 1
        and type(spots) is int
        and spots == reference.spots_left,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("reference", type=Path)
    args = parser.parse_args()
    checks = score(
        json.loads(args.report.read_text()),
        Reference.model_validate_json(args.reference.read_text()),
    )
    print(json.dumps(checks))
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
