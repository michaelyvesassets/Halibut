"""Independent intelligence providers sharing one framework.

Each provider: builds requests, fetches through ``PoliteClient`` (budgets,
rate limits, logging), stores the raw response as a content-addressed
snapshot, and parses it into observations, entities, relationships or
documents with provenance. Providers never call each other.
"""
