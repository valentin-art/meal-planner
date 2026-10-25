import os

DEFAULT_PORT = 8000


def main() -> None:
    """Entry point `meal-planner`: uvicorn на 0.0.0.0:$MEALPLAN_PORT (так в образе)."""
    import uvicorn

    port = int(os.environ.get("MEALPLAN_PORT", DEFAULT_PORT))
    uvicorn.run("meal_planner.api.main:app", host="0.0.0.0", port=port)
