# ADR 0011: Evaluation and Regression Harness

**Status:** Accepted
**Date:** 2026-09-17

## Context

Milestone 2.5 requires a fixed evaluation set covering classification, retrieval hit rate,
groundedness, citation correctness, usefulness, structured-output validity, and safety; a
command that produces machine-readable and human-readable reports; documented thresholds that
block a release when results regress; and a demonstrated failing regression gate (roadmap
Milestone 2.5; rubric §F rows 3-6, §E row 5). Prior milestones already built everything this
needs to reuse: provider/model/prompt provenance (ADR 0008, ADR 0010), a real, measured retrieval
calibration fixture (ADR 0009), and a deterministic mock AI provider. Nothing before this
milestone measured *AI-narrative or retrieval quality* against a fixed set, only mechanism
(routing, fallback, contract validation).

## Decision

### Fixed-set regression, never real-world population drift

This harness measures exactly one thing: whether a fixed, known set of scenarios still produces
the same, previously-verified deterministic and safety-mechanism outcomes after a code/prompt/
model/threshold change. It never claims to measure real-world population drift — this project has
no live traffic or real user population to measure that against, and every report ends with an
explicit disclaimer to that effect (rubric row F6 exists specifically to prevent this
misrepresentation).

### Checked-in, reviewable JSON fixture and baseline

`backend/tests/evaluation/fixed_cases.json` (19 cases, schema-versioned) and
`backend/tests/evaluation/baseline.json` (the checked-in known-good result) are both plain,
git-diffable JSON, mirroring the existing `bge_similarity_calibration.json` pattern. The baseline
is never regenerated silently: ordinary `python -m app.evaluation` runs are read-only against it;
only the explicit `python -m app.evaluation --update-baseline` flag writes it, and only after the
run itself has zero blocking failures — a broken evaluation can never become the new baseline. A
fixture content-hash is embedded in the baseline; any fixture change invalidates the comparison
with a loud, explicit error rather than silently comparing against stale expectations.

### Three explicitly separated evaluation stages, never conflated

`app.evaluation.runner`'s cases exercise two things together: **(A) deterministic triage** —
calling the real `app.triage.rules.classify`/`assess`/`propose` — and **(C) narrative/citation
wiring** — calling the real `app.ai_gateway.router.route_ai_inference` with a *hand-supplied*
`retrieved_context`. Stage C proves that, given a retrieval result, the AI gateway cites only what
it was given and that an invented citation triggers fallback. It was corrected during this ADR's
own review to stop being describable, even implicitly, as testing retrieval quality: the original
runner never called `app.retrieval.service.retrieve_related_evidence` or its selection functions
at all, so an early description of its cases as validating "#5755 → #5756 accepted, #5942
rejected" was an overstatement — those cases only proved that *if* retrieval had already decided
that, citation validation would honor it correctly, which is a real and useful property, but a
different one from retrieval *selecting* correctly.

**(B) retrieval-policy regression** (`app.evaluation.retrieval_policy`, added by this correction)
is the only mechanism that actually calls retrieval selection: it imports and calls the real
`app.retrieval.service._confidence_decision` / `_shared_discriminative_terms` — the exact
functions `retrieve_related_evidence` calls internally — replayed against previously measured,
frozen real-BGE similarity scores and the bounded, real query/candidate text that produced each
recorded outcome (both now stored in `tests/calibration/bge_similarity_calibration.json`'s
`false_positive_regression_examples`, extended with `query_text`/`candidate_text`/
`calibration_schema_version` fields — never with a newly fabricated similarity score; every score
was already real and previously measured during Milestone 2.3). This proves the *current* policy
code still reaches the recorded decision for #5756 (accepted), #5942/#5804/#5836 (rejected), and
#5863 (accepted per the approved discriminative-term rule) — not merely that it once did.

### Deterministic, zero-cost CI path

The default and only CI mode uses `AI_PROVIDER=mock`. No case downloads a model, makes a network
call, or requires an API key. Stage C's two mechanisms unrelated to network dependence but
requiring the router's *real* code paths — a provider failure falling back, and an invented
citation being rejected — are exercised by monkeypatching `httpx.post` before any request would be
sent (`app.evaluation.runner`), the same technique `tests/test_ai_gateway.py` already uses; this
is deliberate production-code technique for this harness, not test code smuggled into `app/`.
Stage B never embeds text, queries pgvector, or opens a database session — `_confidence_decision`
is called directly against a lightweight in-memory stand-in carrying only `.content`. Every report
states plainly that the calibration evidence behind stage B is frozen, not a fresh live-model run.

### Blocking versus warning metrics, precisely named per stage

Blocking (fail CI): from stage A/C, classification/severity/proposed-action exact-match agreement,
required citations present, forbidden citations absent (and independently, that
`validate_citations` would reject them), `citation_validity_rate` at 100%, mock cost at exactly
$0.00, complete case execution, and fixture/baseline schema compatibility. From stage B,
`retrieval_policy_expected_accept_rate` and `retrieval_policy_forbidden_rejection_rate` both at
100% for every non-limitation case — a known-positive candidate becoming rejected, or a
known-negative candidate becoming accepted, is exactly the class of regression Milestone 2.3
itself demonstrated live (issue #5942). For all of these, the threshold is **no regression from
the checked-in baseline** rather than an arbitrary numeric tolerance, because the underlying logic
(Milestone 1's deterministic rules, Milestone 2.2's citation validator, Milestone 2.3's retrieval
selection policy) is rule-based code, not a statistical model — any deviation is a real defect,
not noise. Metric names are deliberately precise about what they measure: `citation_validity_rate`
never claims to be "retrieval precision," and the two `retrieval_policy_*` names never claim to be
a general recall/precision report — they are narrowly what they say: whether specific known-good
and known-bad candidates are still handled correctly by the current policy code. Warning-only
(reported, never blocking): `narrative_rule_violations` (simple required/forbidden substring
checks against the mock adapter's deterministic template — explicitly *not* a semantic factuality
or "unsupported-claim" measurement, which remains unproven without a stronger evaluator than this
project has), latency (p50/p95, inherently nondeterministic wall-clock data), and token/cost
totals beyond the zero-cost invariant already above.

### Known, disclosed limitations stay visible, never silently required

Two representations of the same disclosed #6139 polysemy limitation exist, one per relevant stage:
`known_limitation_6139_polysemy` (stage A/C, `"blocking": false`) and the
`retrieval_policy_issue_5755_to_issue_6139` case (stage B, `"known_limitation": true` in the
calibration fixture). Both are reported in every run under "Known, disclosed limitations" but
never gate pass/fail in either direction — accepting #6139 is not rewarded as a required success,
and if it ever stopped being accepted that would not be flagged as a regression either. This
documents the limitation honestly rather than either quietly dropping it or asserting a specific
retrieval relationship this milestone did not fix.

### Provenance reused, not duplicated

`EvaluationReport` never recomputes provider/model/prompt identity — every stage A/C result
carries the exact `prompt_id`/`prompt_version`/`prompt_status`/`prompt_template_hash`/
`rendered_prompt_hash` fields Milestone 2.4's `AIResponse` already produces, and stage B carries
`calibration_schema_version` from the calibration fixture rather than inventing a parallel
versioning scheme. A prompt or provider identity change is reported as a note, never treated as a
regression by itself — the metrics above are what actually gate pass/fail.

## Alternatives considered

**An LLM-as-judge grading layer** was rejected: it would add a second, harder-to-trust AI
dependency to grade the first one, and would itself need evaluating — disproportionate for a
project this size, and it would blur exactly the "mocked quality vs. real quality" distinction
this ADR is careful to keep explicit. **A database-backed evaluation-run history** was rejected,
matching ADR 0008's and ADR 0010's prior reasoning: nothing needs indexed cross-run queries at
this scale; a checked-in file per run is enough, and a reviewable git diff is a *better* audit
trail than a database row for this purpose. **A frontend evaluation dashboard** was rejected: no
rubric row or roadmap bullet asks for one, and the printed/JSON report is sufficient evidence.
**Enterprise-style drift/monitoring infrastructure** (statistical drift detectors, time-series
tracking) was rejected: there is no real production distribution behind it here, and building one
would misrepresent what the project can honestly claim to measure. **A single "prompt/rendered
hash"** was rejected in favor of reusing ADR 0010's existing two-hash distinction rather than
inventing a third evaluation-specific one.

## Consequences

`backend/app/evaluation/` is a new, self-contained package requiring no database access at all —
every case (stage A/C) and every retrieval-policy replay (stage B) runs against a lightweight
in-memory stand-in, not the demo database, so running the evaluation never touches or requires the
live demo database's 100 issues/405 chunks. `tests/calibration/bge_similarity_calibration.json`
gained `calibration_schema_version` and, per `false_positive_regression_examples` entry, a
`query_text`/`candidate_text` pair (plus a new `issue:6139` entry using its already-measured,
already-observed real score) — additive fields only, no existing score changed. CI gains one new,
fast (sub-second), zero-cost step. `.github/workflows/ci.yml`'s Postgres service image was
corrected from plain `postgres:17.7-alpine` to the pinned `pgvector/pgvector:0.8.6-pg17-bookworm`
image `compose.yaml` already uses — a prerequisite fix, not new scope: migration `20260916_0004`
requires the `vector` extension, which the prior CI image did not provide.

## Security implications

No new untrusted-data surface: all case inputs are either real, already-imported/sanitized issue
text (Milestone 1.2) or clearly-synthetic evaluation scenarios. The simulated-provider-failure/
invented-citation mechanism never sends a real request or a real API key anywhere — `httpx.post`
is replaced before any request object is built, using the same fake placeholder value
(`"sk-test"`) the existing AI-gateway test suite already uses. No raw rendered prompt text is
persisted by the evaluation report, matching ADR 0010's policy exactly.

## Testing/evidence

Stage A/C (`app.evaluation.runner`, `tests/test_evaluation.py`): fixture schema validation (unique
case IDs, valid categories, non-empty rationale, mutually exclusive simulation flags);
deterministic repeated-run identity (byte-identical blocking results and rendered-prompt hashes
across runs); the real 19-case fixture passing every case on measurement (every expected value was
obtained by running the real `classify`/`assess`/`propose` functions against real issue text, not
guessed); intentionally degraded classification/severity cases failing; a required citation
disappearing failing; a forbidden citation appearing failing (and `validate_citations`
independently proven to reject it); an invented simulated citation correctly falling back (and
therefore *passing*, since graceful fallback is the correct outcome); a simulated provider failure
correctly falling back; nonzero mock cost never occurring on the real path; the known-limitation
case never contributing a blocking failure regardless of its own classification outcome;
narrative-rule violations always landing as warnings, never blocking.

Stage B (`app.evaluation.retrieval_policy`): the real `_confidence_decision` correctly reproduces
all six frozen, previously recorded outcomes (#5756/#5863/#6139 accepted, #5942/#5804/#5836
rejected) when replayed against the frozen scores and bounded text; #5942 becoming accepted (a
temporarily, in-memory-reverted corpus-generic-term-set regression, mirroring the actual Milestone
2.3 bug) produces a named, blocking `retrieval_policy_forbidden_rejection_rate` regression, not a
citation/fallback result; the #6139 known-limitation case's outcome never blocks in either
direction; a report clearly labels stage B's evidence as frozen/previously measured, never a fresh
model run, and confirms zero model download, network request, or paid call occurs anywhere in the
process; the report's three sections (A/B/C) are asserted to be distinguishable, so retrieval
selection is never again conflated with citation wiring.

Consolidated report: candidate/baseline provenance fields present for both stages; a missing
baseline, an incompatible report/fixture/calibration schema version, a changed fixture hash, and
an incomplete case set (either stage) each raising a clear, distinct error; a regressed case that
passed in the baseline producing a named blocking regression, tagged `[triage/narrative]` or
`[retrieval-policy]` so its origin is never ambiguous; latency differences never causing a
regression; prompt/provider identity changes reported as notes, not regressions; the CLI never
writing the baseline on an ordinary run, writing it only on `--update-baseline` (and refusing to if
either stage has blocking failures), and returning 0 on a clean pass / nonzero on a blocking
regression from either stage. Two live, temporary, in-memory demonstrations (each reverted before
completion, no file ever touched) proved a genuine intentional regression is caught with the exact
case and metric named and exits non-zero — one for stage A/C (a deterministic-action regression),
one for stage B (the #5942/#5836 false-positive regression, reproduced by temporarily reverting
the real Milestone 2.3 fix in memory).

## Revisit conditions

Revisit only through an explicitly approved ADR. A second, larger evaluation set, a documented
process for reviewing intentional baseline updates, and genuinely quantitative retrieval-hit-rate
metrics (beyond citing the frozen calibration fixture) are natural extensions once this mechanism
has run for a while, not gaps to fix now. An optional, explicitly-authorized real-provider
evaluation mode (never CI, never default, budget-limited, requiring `AI_PROVIDER=openai` plus
`OPENAI_API_KEY` exactly like the existing gateway) may be added later without changing this
harness's default zero-cost contract. `narrative_rule_violations` remaining warning-only should be
revisited only after real measurement across several implementation iterations shows the
substring-check approach doesn't produce false positives — promoting it to blocking prematurely
risks exactly the flaky-threshold failure mode this ADR is designed to avoid. Milestone 2.6's
guardrail/prompt-injection fixture suite is a distinct, later concern and should build on this
harness's case format rather than duplicating it.
