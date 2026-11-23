"""Intelligence agents (Research, Hypothesis, Critic) over a shared, replaceable LLM provider.

Boundaries:
- agents read evidence/knowledge through as-of retrievers and return JSON;
- the engine (not the agent) builds predictions, validates citations and
  numbers, and records everything as PROPOSED knowledge;
- every LLM call is cached and accounted (tokens, cost, latency, cache hits,
  duplicate tool calls) in ``agent_calls``;
- no outbound actions; network research actions are only proposed.
"""
