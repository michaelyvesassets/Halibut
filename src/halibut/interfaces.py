"""Extension points for future AI integrations (e.g. Qwen). Interfaces only.

Design rule: AI components may *propose* inputs or *interpret* outputs, but
they never sit inside the simulation loop. Anything an AI component proposes
must be expressed as configuration and pass through ``halibut.config``
validation like hand-written input, so every run stays reproducible from its
manifest. No implementation in this package calls an external service.
"""

from __future__ import annotations

from typing import Protocol


class ScenarioProposer(Protocol):
    """Turns a natural-language brief into scenario tables (TOML-shaped dicts)."""

    def propose(self, brief: str, base_config: dict) -> list[dict]: ...


class PopulationPrior(Protocol):
    """Suggests consumer segment definitions (TOML-shaped dicts) for a market."""

    def segments(self, market_description: str) -> list[dict]: ...


class ResultInterpreter(Protocol):
    """Produces a narrative reading of an experiment summary (``summary.json``).

    Interpretations must preserve the summary's synthetic-result labelling.
    """

    def interpret(self, summary: dict) -> str: ...
