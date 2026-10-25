from datetime import date

from meal_planner.core.models import Settings


def settings_of(scenario) -> Settings:
    """Settings из сценария golden_scheduling (дата якоря — ISO-строка)."""
    raw = scenario["settings"]
    return Settings(
        **{**raw, "meal_anchor_date": date.fromisoformat(raw["meal_anchor_date"])}
    )
