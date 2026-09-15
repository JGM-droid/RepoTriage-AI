"""Aggregate metrics, the candidate/baseline JSON report shape, and
comparison logic -- kept in three explicitly separated sections (ADR
0011):

  A. Triage metrics (`TriageNarrativeMetrics.classification_accuracy` /
     `.severity_accuracy` / `.action_accuracy`) -- from
     `app.evaluation.runner`'s cases, which call the real `app.triage.
     rules.classify/assess/propose`.

  B. Retrieval-policy metrics (`RetrievalPolicyMetrics.
     retrieval_policy_expected_accept_rate` / `.
     retrieval_policy_forbidden_rejection_rate`) -- from
     `app.evaluation.retrieval_policy`, which replays the real `app.
     retrieval.service._confidence_decision` against frozen, previously
     measured real-BGE similarity scores. This is the only section that
     says anything about retrieval *selection* quality.

  C. Narrative/citation metrics (`TriageNarrativeMetrics.
     citation_validity_rate` / `.mock_cost_total_usd` / warnings) -- also
     from `app.evaluation.runner`'s cases, which supply a hand-built
     `retrieved_context` directly to the AI gateway and check citation
     validation/fallback behavior. This never re-derives what retrieval
     *should* have returned -- see section B for that.

Blocking policy: for exact-match deterministic properties (classification/
severity/action agreement, required/forbidden citations, citation
validity, mock cost, and retrieval-policy accept/reject outcomes for
non-limitation cases), the threshold is "no regression from the checked-in
baseline" -- not an arbitrary numeric tolerance -- because the underlying
logic is deterministic rule-based code, not a statistical model: any
deviation is a real defect, not noise. Narrative-rule violations, latency,
and token/cost totals beyond the zero-cost invariant are always
observational -- included in the report, never blocking. The #6139
known-limitation retrieval-policy case is always informational, in either
direction, regardless of section.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.ai_gateway.prompts import get_active_prompt
from app.evaluation.contracts import CaseResult, EvaluationFixture
from app.evaluation.retrieval_policy import RetrievalPolicyFixture, RetrievalPolicyResult

REPORT_SCHEMA_VERSION = "2.0.0"

DRIFT_DISCLAIMER = (
    "This report measures regression against a fixed evaluation set; "
    "it does not measure real-world population drift."
)

FROZEN_CALIBRATION_DISCLAIMER = (
    "Section B replays the real, current retrieval-selection policy "
    "(app.retrieval.service._confidence_decision) against similarity scores "
    "that are frozen, previously-measured real-BGE-model output (see "
    "tests/calibration/bge_similarity_calibration.json) -- this run makes no "
    "model download, no embedding call, and no live retrieval query; it "
    "never re-measures a similarity score, only re-derives the accept/"
    "reject decision from the current policy code and the frozen score."
)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[index]


# --- A + C: triage and narrative/citation metrics (app.evaluation.runner) ----


@dataclass(frozen=True)
class TriageNarrativeMetrics:
    """Covers stage A (deterministic triage) and stage C (narrative/
    citation wiring) together, since an individual `EvaluationCase` often
    exercises both. Never covers stage B (retrieval-policy selection) --
    see `RetrievalPolicyMetrics`."""

    total_cases: int
    passed_cases: int
    failed_cases: int
    known_limitation_cases: int
    total_warnings: int
    classification_accuracy: float
    severity_accuracy: float
    action_accuracy: float
    citation_validity_rate: float
    mock_cost_total_usd: float
    latency_p50_ms: float
    latency_p95_ms: float
    total_input_tokens: int
    total_output_tokens: int


def compute_triage_narrative_metrics(results: tuple[CaseResult, ...]) -> TriageNarrativeMetrics:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    known_limitation = sum(1 for r in results if r.known_limitation)
    total_warnings = sum(len(r.warnings) for r in results)

    citation_relevant_results = [r for r in results if r.citation_relevant]
    citation_checks = [
        0 if any("citation" in f.lower() for f in r.blocking_failures) else 1
        for r in citation_relevant_results
    ]
    classification_checks = [
        0 if any(f.startswith("classification.label") for f in r.blocking_failures) else 1
        for r in results
    ]
    severity_checks = [
        0 if any(f.startswith("assessment.severity") for f in r.blocking_failures) else 1
        for r in results
    ]
    action_checks = [
        0 if any(f.startswith("proposed_action.action") for f in r.blocking_failures) else 1
        for r in results
    ]
    latencies = [r.latency_ms for r in results]

    return TriageNarrativeMetrics(
        total_cases=total,
        passed_cases=passed,
        failed_cases=total - passed,
        known_limitation_cases=known_limitation,
        total_warnings=total_warnings,
        classification_accuracy=sum(classification_checks) / total if total else 1.0,
        severity_accuracy=sum(severity_checks) / total if total else 1.0,
        action_accuracy=sum(action_checks) / total if total else 1.0,
        citation_validity_rate=(
            (sum(citation_checks) / len(citation_checks)) if citation_checks else 1.0
        ),
        mock_cost_total_usd=sum(r.estimated_cost_usd for r in results),
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        total_input_tokens=sum(r.input_tokens for r in results),
        total_output_tokens=sum(r.output_tokens for r in results),
    )


# --- B: retrieval-policy metrics (app.evaluation.retrieval_policy) ----------


@dataclass(frozen=True)
class RetrievalPolicyMetrics:
    """The only section that says anything about retrieval *selection*
    quality. Both rates exclude known-limitation cases (#6139) from their
    denominator -- that case is always reported, never counted as a
    required correct match in either direction."""

    total_cases: int
    known_limitation_cases: int
    retrieval_policy_expected_accept_rate: float
    retrieval_policy_forbidden_rejection_rate: float


def compute_retrieval_policy_metrics(
    results: tuple[RetrievalPolicyResult, ...],
) -> RetrievalPolicyMetrics:
    scored = [r for r in results if not r.known_limitation]
    expected_accept = [r for r in scored if r.expected_decision == "accepted"]
    expected_reject = [r for r in scored if r.expected_decision == "rejected"]
    accept_hits = sum(1 for r in expected_accept if r.actual_decision == "accepted")
    reject_hits = sum(1 for r in expected_reject if r.actual_decision == "rejected")

    return RetrievalPolicyMetrics(
        total_cases=len(results),
        known_limitation_cases=sum(1 for r in results if r.known_limitation),
        retrieval_policy_expected_accept_rate=(
            accept_hits / len(expected_accept) if expected_accept else 1.0
        ),
        retrieval_policy_forbidden_rejection_rate=(
            reject_hits / len(expected_reject) if expected_reject else 1.0
        ),
    )


# --- consolidated report ------------------------------------------------------


@dataclass(frozen=True)
class EvaluationReport:
    """The candidate (or baseline) run's full report, kept in three
    explicitly separated sections (see module docstring). Latency is
    recorded per-case for observability but deliberately excluded from the
    comparison logic in `compare()` -- it is nondeterministic wall-clock
    data and must never fail deterministic CI."""

    report_schema_version: str
    fixture_schema_version: str
    fixture_version: str
    fixture_hash: str
    calibration_schema_version: str
    provider: str
    model: str
    prompt_id: str
    prompt_version: str
    prompt_status: str
    prompt_template_hash: str
    case_results: tuple[CaseResult, ...]
    triage_narrative_metrics: TriageNarrativeMetrics
    retrieval_policy_results: tuple[RetrievalPolicyResult, ...]
    retrieval_policy_metrics: RetrievalPolicyMetrics
    known_limitations: tuple[str, ...]
    generated_by_command: str


def build_report(
    fixture: EvaluationFixture,
    fixture_hash: str,
    results: tuple[CaseResult, ...],
    retrieval_policy_fixture: RetrievalPolicyFixture,
    retrieval_policy_results: tuple[RetrievalPolicyResult, ...],
    *,
    command: str,
) -> EvaluationReport:
    active_prompt = get_active_prompt("triage_narrative")
    known_limitations = tuple(
        r.case_id for r in (*results, *retrieval_policy_results) if r.known_limitation
    )
    return EvaluationReport(
        report_schema_version=REPORT_SCHEMA_VERSION,
        fixture_schema_version=fixture.schema_version,
        fixture_version=fixture.fixture_version,
        fixture_hash=fixture_hash,
        calibration_schema_version=retrieval_policy_fixture.calibration_schema_version,
        provider="mock",
        model="deterministic-v1",
        prompt_id=active_prompt.prompt_id,
        prompt_version=active_prompt.version,
        prompt_status=active_prompt.status,
        prompt_template_hash=active_prompt.template_hash,
        case_results=results,
        triage_narrative_metrics=compute_triage_narrative_metrics(results),
        retrieval_policy_results=retrieval_policy_results,
        retrieval_policy_metrics=compute_retrieval_policy_metrics(retrieval_policy_results),
        known_limitations=known_limitations,
        generated_by_command=command,
    )


def report_to_dict(report: EvaluationReport) -> dict:
    return asdict(report)


@dataclass(frozen=True)
class ComparisonResult:
    blocking_regressions: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.blocking_regressions


class BaselineIncompatibleError(RuntimeError):
    """The baseline cannot be meaningfully compared against (missing,
    wrong schema, or the fixture/calibration evidence changed without an
    intentional baseline review) -- always treated as a blocking failure,
    never silently ignored."""


def compare(baseline: dict, candidate: EvaluationReport) -> ComparisonResult:
    regressions: list[str] = []
    notes: list[str] = []

    if baseline.get("report_schema_version") != candidate.report_schema_version:
        raise BaselineIncompatibleError(
            f"baseline report_schema_version {baseline.get('report_schema_version')!r} != "
            f"candidate {candidate.report_schema_version!r} -- regenerate the baseline "
            "with --update-baseline after reviewing the change."
        )
    if baseline.get("fixture_hash") != candidate.fixture_hash:
        raise BaselineIncompatibleError(
            "the evaluation fixture changed (fixture_hash differs from the checked-in "
            "baseline) without an intentional baseline review -- run "
            "`python -m app.evaluation --update-baseline` only after reviewing why the "
            "fixture changed, then commit the resulting baseline diff for review."
        )

    # --- A + C: triage / narrative / citation case-level comparison ----------
    baseline_case_ids = {c["case_id"] for c in baseline.get("case_results", [])}
    candidate_case_ids = {r.case_id for r in candidate.case_results}
    if baseline_case_ids != candidate_case_ids:
        missing = baseline_case_ids - candidate_case_ids
        extra = candidate_case_ids - baseline_case_ids
        raise BaselineIncompatibleError(
            f"case set changed: missing from candidate={sorted(missing)!r}, "
            f"new in candidate={sorted(extra)!r} -- not all baseline cases ran, or the "
            "fixture changed; review and update the baseline explicitly if intentional."
        )

    baseline_by_id = {c["case_id"]: c for c in baseline["case_results"]}
    for result in candidate.case_results:
        base = baseline_by_id[result.case_id]
        base_passed = not base.get("blocking_failures")
        if base_passed and not result.passed:
            regressions.append(
                f"[triage/narrative] {result.case_id}: passed in baseline, now failing: "
                f"{list(result.blocking_failures)}"
            )
        elif not base_passed and not result.passed:
            notes.append(f"[triage/narrative] {result.case_id}: still failing in both")

        if set(base.get("citations", [])) != set(result.citations) and (
            base_passed or result.passed
        ):
            notes.append(
                f"[triage/narrative] {result.case_id}: citations changed "
                f"({base.get('citations')} -> {list(result.citations)})"
            )

    if candidate.triage_narrative_metrics.mock_cost_total_usd != 0.0:
        regressions.append(
            "[narrative] mock_cost_total_usd is nonzero: "
            f"{candidate.triage_narrative_metrics.mock_cost_total_usd!r}"
        )
    if candidate.triage_narrative_metrics.citation_validity_rate < 1.0:
        regressions.append(
            "[narrative] citation_validity_rate dropped below 100%: "
            f"{candidate.triage_narrative_metrics.citation_validity_rate:.2%}"
        )

    # --- B: retrieval-policy case-level comparison ----------------------------
    if baseline.get("calibration_schema_version") != candidate.calibration_schema_version:
        raise BaselineIncompatibleError(
            "the retrieval-policy calibration evidence's schema version changed "
            f"(baseline={baseline.get('calibration_schema_version')!r} != "
            f"candidate={candidate.calibration_schema_version!r}) without an intentional "
            "baseline review -- run --update-baseline only after reviewing why."
        )
    baseline_rp_ids = {c["case_id"] for c in baseline.get("retrieval_policy_results", [])}
    candidate_rp_ids = {r.case_id for r in candidate.retrieval_policy_results}
    if baseline_rp_ids != candidate_rp_ids:
        raise BaselineIncompatibleError(
            "retrieval-policy case set changed: missing from candidate="
            f"{sorted(baseline_rp_ids - candidate_rp_ids)!r}, new in candidate="
            f"{sorted(candidate_rp_ids - baseline_rp_ids)!r} -- the calibration evidence "
            "changed or not all retrieval-policy cases ran; review and update the baseline "
            "explicitly if intentional."
        )
    baseline_rp_by_id = {c["case_id"]: c for c in baseline["retrieval_policy_results"]}
    for rp_result in candidate.retrieval_policy_results:
        base = baseline_rp_by_id[rp_result.case_id]
        base_passed = not base.get("blocking_failures")
        if base_passed and not rp_result.passed:
            regressions.append(
                f"[retrieval-policy] {rp_result.case_id}: passed in baseline, now failing: "
                f"{list(rp_result.blocking_failures)} (expected={rp_result.expected_decision!r}, "
                f"actual={rp_result.actual_decision!r})"
            )
        elif not base_passed and not rp_result.passed:
            notes.append(f"[retrieval-policy] {rp_result.case_id}: still failing in both")

    # --- provenance notes (never regressions by themselves) ------------------
    if candidate.prompt_version != baseline.get(
        "prompt_version"
    ) or candidate.prompt_id != baseline.get("prompt_id"):
        notes.append(
            f"prompt identity changed: baseline={baseline.get('prompt_id')}@"
            f"{baseline.get('prompt_version')} -> candidate={candidate.prompt_id}@"
            f"{candidate.prompt_version} (reported, not itself treated as a regression -- "
            "the metrics above are what determine pass/fail)"
        )
    if candidate.provider != baseline.get("provider") or candidate.model != baseline.get("model"):
        notes.append(
            f"provider/model identity changed: baseline={baseline.get('provider')}/"
            f"{baseline.get('model')} -> candidate={candidate.provider}/{candidate.model}"
        )

    return ComparisonResult(blocking_regressions=tuple(regressions), notes=tuple(notes))


def format_human_summary(
    candidate: EvaluationReport, *, baseline: dict | None, comparison: ComparisonResult | None
) -> str:
    lines: list[str] = []
    lines.append("RepoTriage AI -- Evaluation and Regression Report")
    lines.append("=" * 60)
    lines.append(
        f"Provider/model: {candidate.provider}/{candidate.model}   "
        f"Prompt: {candidate.prompt_id}@{candidate.prompt_version} ({candidate.prompt_status})"
    )
    lines.append(
        f"Fixture: version={candidate.fixture_version} hash={candidate.fixture_hash[:12]}...   "
        f"Calibration schema: {candidate.calibration_schema_version}"
    )

    tn = candidate.triage_narrative_metrics
    lines.append("")
    lines.append(
        "A. Deterministic triage (app.triage.rules.classify/assess/propose, real functions):"
    )
    lines.append(
        f"   Classification accuracy: {tn.classification_accuracy:.0%}   "
        f"Severity accuracy: {tn.severity_accuracy:.0%}   "
        f"Action accuracy: {tn.action_accuracy:.0%}"
    )

    rp = candidate.retrieval_policy_metrics
    lines.append("")
    lines.append(
        "B. Retrieval-policy regression (app.retrieval.service._confidence_decision, "
        "replayed against frozen real-BGE scores -- see calibration disclaimer below):"
    )
    lines.append(
        f"   {rp.total_cases} case(s), {rp.known_limitation_cases} known limitation(s)   "
        f"retrieval_policy_expected_accept_rate: {rp.retrieval_policy_expected_accept_rate:.0%}   "
        f"retrieval_policy_forbidden_rejection_rate: "
        f"{rp.retrieval_policy_forbidden_rejection_rate:.0%}"
    )
    for r in candidate.retrieval_policy_results:
        marker = "KNOWN LIMITATION" if r.known_limitation else ("PASS" if r.passed else "FAIL")
        lines.append(
            f"   [{marker}] {r.case_id}: frozen_score={r.frozen_similarity_score:.4f} "
            f"expected={r.expected_decision} actual={r.actual_decision} "
            f"(tier={r.actual_tier}, shared_terms={list(r.shared_discriminative_terms)})"
        )

    lines.append("")
    lines.append(
        "C. Narrative/citation behavior (app.ai_gateway.router.route_ai_inference, "
        "with hand-supplied retrieved_context -- NOT retrieval selection, see B):"
    )
    lines.append(
        f"   Citation validity: {tn.citation_validity_rate:.0%}   "
        f"Mock cost total: ${tn.mock_cost_total_usd:.4f}   "
        f"Tokens: {tn.total_input_tokens} in / {tn.total_output_tokens} out"
    )
    lines.append(
        f"   {tn.total_warnings} narrative_rule_violation warning(s) -- warn-only, never blocking"
    )
    lines.append(
        f"   Latency (informational only, never blocking): p50={tn.latency_p50_ms:.2f}ms "
        f"p95={tn.latency_p95_ms:.2f}ms"
    )

    lines.append("")
    lines.append(
        f"Cases (A+C combined): {tn.total_cases} total, {tn.passed_cases} passed, "
        f"{tn.failed_cases} failed, {tn.known_limitation_cases} known limitation(s)"
    )

    if candidate.known_limitations:
        lines.append("")
        lines.append("Known, disclosed limitations (nonblocking, tracked for visibility):")
        for case_id in candidate.known_limitations:
            lines.append(f"  - {case_id}")

    all_blocking = [(r.case_id, f) for r in candidate.case_results for f in r.blocking_failures] + [
        (r.case_id, f) for r in candidate.retrieval_policy_results for f in r.blocking_failures
    ]
    if all_blocking:
        lines.append("")
        lines.append("Blocking failures:")
        for case_id, failure in all_blocking:
            lines.append(f"  [{case_id}] {failure}")

    all_warnings = [(r.case_id, w) for r in candidate.case_results for w in r.warnings] + [
        (r.case_id, w) for r in candidate.retrieval_policy_results for w in r.warnings
    ]
    if all_warnings:
        lines.append("")
        lines.append("Warnings (do not block):")
        for case_id, warning in all_warnings:
            lines.append(f"  [{case_id}] {warning}")

    if baseline is not None and comparison is not None:
        lines.append("")
        lines.append(
            f"Baseline comparison: {'PASS' if comparison.passed else 'FAIL (blocking regression)'}"
        )
        if comparison.blocking_regressions:
            lines.append("Blocking regressions vs. baseline:")
            for reg in comparison.blocking_regressions:
                lines.append(f"  - {reg}")
        if comparison.notes:
            lines.append("Notes (informational, not blocking):")
            for note in comparison.notes:
                lines.append(f"  - {note}")

    lines.append("")
    lines.append(FROZEN_CALIBRATION_DISCLAIMER)
    lines.append("")
    lines.append(DRIFT_DISCLAIMER)

    return "\n".join(lines)
