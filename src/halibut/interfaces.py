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


# --------------------------------------------------------------------------- Phase 4: agent interfaces
#
# Agents (deterministic reference agents now; Qwen-powered agents later) are
# TOOLS over the shared store, not autonomous actors:
# - they READ evidence and knowledge through retrievers bounded by an as-of date;
# - they WRITE only proposals (hypotheses, predictions, critiques, questions,
#   improvement proposals) into the learning memory with status 'proposed';
# - they never write observations, never validate their own claims, and never
#   take outbound actions (posting, messaging, purchasing, contacting people).
# Every agent output passes halibut.validation before it is recorded.


class EvidenceRetriever(Protocol):
    as_of: str

    def observations(self, series_id: str, geo: str) -> list[dict]: ...

    def relationships(self, subject: str | None = None, predicate: str | None = None) -> list[dict]: ...

    def documents(self, entity_id: str, start: str, end: str) -> list[dict]: ...

    def entity(self, entity_id: str) -> dict | None: ...

    def exists(self, evidence_id: str) -> bool: ...


class KnowledgeRetriever(Protocol):
    """Historical lessons, outcomes, critiques and open questions (any status)."""

    def retrieve(self, *, tags: list[str] | None = None, kinds: list[str] | None = None,
                 statuses: list[str] | None = None, limit: int = 50) -> list[dict]: ...


class HypothesisAgent(Protocol):
    """Evidence bundle (signals, entity graph, lessons) -> structured hypotheses (status 'proposed')."""

    id: str

    def propose(self, context: dict) -> list[dict]: ...


class CriticAgent(Protocol):
    """Hypothesis + context -> critiques (preserved alongside, never overwriting, the hypothesis)."""

    id: str

    def critique(self, hypothesis: dict, context: dict) -> list[dict]: ...


class ScenarioDrafter(Protocol):
    """Hypothesis -> draft simulation config (raw TOML dict, validated by parse_config, labelled synthetic)."""

    id: str

    def draft(self, hypothesis: dict, context: dict) -> dict | None: ...


class ImprovementProposer(Protocol):
    """Learning memory -> next controlled experiment (change, rationale, expected improvement)."""

    id: str

    def propose(self, memory, tried: list) -> dict: ...
