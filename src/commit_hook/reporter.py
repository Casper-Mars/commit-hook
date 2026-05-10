"""Terminal output formatting for commit-hook."""

from typing import Any


def report_results(results: dict[str, Any]) -> None:
    """Format and display validation results in the terminal.

    Args:
        results: A dictionary containing the validation results.
    """
    del results
