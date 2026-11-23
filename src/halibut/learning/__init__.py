"""Learning / Evaluation Engine and Shared Learning Memory.

Four distinct steps, each recorded separately:
1. RECORD an experience     -> experiments (with lineage) and outcomes
2. EXTRACT a hypothesis/lesson -> knowledge rows with status 'proposed'
3. VALIDATE generalisation  -> evaluations under explicit rules
4. UPDATE a model/policy    -> promotions, only with an eligible evaluation
                               AND explicit approval; always reversible

Accumulating records is not learning: a lesson counts as validated only when
an evaluation under the documented rules supports it.
"""
