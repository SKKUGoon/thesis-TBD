"""Load the existing API and input contracts and validate real JSON payloads."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import json
import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

SEOUL = ZoneInfo("Asia/Seoul")
SPEC = json.loads(Path(os.getenv("CONTRACT_PATH", "architect/06_contracts/openapi.json")).read_text())
FEATURES = SPEC["paths"]["/model-versions/{model_version_id}/input-contract"]["get"]["responses"]["200"]["content"]["application/json"]["example"]["features"]


class Conflict(Exception):
    """A domain precondition failed without a partial business commit."""


def validate(name: str, body: dict) -> None:
    schema = {"$ref": f"#/components/schemas/{name}", "components": SPEC["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(body)


def at(day: date, hour: int = 0) -> datetime:
    return datetime.combine(day, time(hour), SEOUL)


def parsed(value: str | datetime) -> datetime:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days)]


def calendar_values(day: date, knowledge: datetime) -> dict[str, float]:
    """Use the versioned 2020–2024 calendar with announcement availability gates."""
    path = Path(os.getenv("CALENDAR_PATH", "/app/reference-calendar.json"))
    calendar = json.loads(path.read_text())
    if not 2020 <= day.year <= 2024:
        raise Conflict("CALENDAR_RANGE_UNSUPPORTED")
    holidays = {date.fromisoformat(r["date"][:10]) for r in calendar["holidays"] if parsed(r["available_at"]).replace(tzinfo=SEOUL) <= knowledge}
    policy, last = 0., 0.
    for row in calendar["policy"]:
        if parsed(row["available_at"]).replace(tzinfo=SEOUL) <= knowledge:
            effective = date.fromisoformat(row["effective_date"][:10])
            if day >= effective:
                policy = float(row["value"])
            if knowledge.astimezone(SEOUL).date() - timedelta(days=1) >= effective:
                last = float(row["value"])
    values = {"target_public_holiday": float(day in holidays or day.weekday() == 6),
              "target_named_holiday": float(day in holidays),
              "target_before_named_holiday": float(day + timedelta(days=1) in holidays),
              "target_after_named_holiday": float(day - timedelta(days=1) in holidays),
              "target_weekend": float(day.weekday() >= 5),
              "is_national_epidemic_distancing_response": policy, "last_day_distancing_response": last,
              "year_sin": math.sin(2 * math.pi * day.timetuple().tm_yday / 365.25),
              "year_cos": math.cos(2 * math.pi * day.timetuple().tm_yday / 365.25)}
    values.update({f"weekday_{i}": float(day.weekday() == i) for i in range(7)})
    return values
