"""Evaluation and regression harness (Milestone 2.5; see ADR 0011).

Runs a fixed, versioned set of deterministic scenarios through the real
triage pipeline (classification/severity/proposed-action rules, the
prompt registry, the AI gateway's mock adapter, and citation validation)
and compares the result against a checked-in baseline, so a prompt/model/
retrieval-threshold change that silently degrades quality is caught before
it ships.

This measures *fixed-set behavior regression*, never real-world population
drift -- the project has no live traffic to measure that against. Real
retrieval semantic quality (similarity scores, hit rate) is represented by
the frozen calibration evidence already produced by Milestone 2.3 (see
`tests/calibration/bge_similarity_calibration.json`), not re-measured
here; this harness never downloads or runs the real BGE model.
"""
