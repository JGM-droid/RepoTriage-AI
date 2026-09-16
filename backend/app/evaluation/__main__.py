"""Evaluation and regression harness CLI: `python -m app.evaluation`.

Runs two distinct, separately reported evaluation mechanisms (see ADR
0011 and `app.evaluation.report`'s module docstring):

  A + C. `app.evaluation.runner`'s fixed case set -- the real deterministic
     triage rules, prompt registry, and AI gateway, with a hand-supplied
     `retrieved_context` -- proves triage correctness and citation/
     fallback wiring, never retrieval selection.

  B. `app.evaluation.retrieval_policy` -- replays the real retrieval-
     selection policy against frozen, previously measured real-BGE
     similarity scores from `tests/calibration/bge_similarity_
     calibration.json`. This is the only mechanism that says anything
     about retrieval-selection quality.

  D. `app.evaluation.adversarial` -- a versioned fixture of prompt-
     injection, fabricated-citation, malformed/oversized-output,
     secret-redaction, and resource-exhaustion cases (Milestone 2.6, ADR
     0012), run through the same real production code as A/C. Every case
     is deterministic and blocking. These checks validate application
     guardrails; they do not prove that every real model will resist every
     prompt-injection technique.

Default mode (no flags): runs all three (A+C, B, D), compares the consolidated result
against the checked-in baseline (`backend/tests/evaluation/baseline.json`),
prints a human-readable report, and exits non-zero only on a blocking
regression. Requires an existing baseline -- it never silently compares a
run against itself.

`--update-baseline`: the *only* way the baseline file changes. Runs the
full evaluation first (so a broken evaluation can never be baselined),
then overwrites the baseline file. This is a deliberate, explicit,
human-reviewed operation -- never run automatically in CI -- and produces
a normal, reviewable git diff.

Zero network, zero paid calls, zero model downloads: the default run uses
`AI_PROVIDER=mock` only; the fallback/citation-rejection paths are
exercised via deterministic, offline `httpx.post` simulation (see
`app.evaluation.runner`), never a real request; the retrieval-policy
section never embeds text, queries pgvector, or loads a model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.evaluation.adversarial import (
    DEFAULT_ADVERSARIAL_FIXTURE_PATH,
    load_adversarial_fixture,
    run_all_adversarial_cases,
)
from app.evaluation.cases import DEFAULT_FIXTURE_PATH, load_fixture
from app.evaluation.report import (
    BaselineIncompatibleError,
    build_report,
    compare,
    format_human_summary,
    report_to_dict,
)
from app.evaluation.retrieval_policy import (
    DEFAULT_CALIBRATION_PATH,
    load_retrieval_policy_fixture,
    run_all_retrieval_policy_cases,
)
from app.evaluation.runner import run_all

DEFAULT_BASELINE_PATH = DEFAULT_FIXTURE_PATH.parent / "baseline.json"


def _load_baseline(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_baseline(path: Path, report_dict: dict) -> None:
    path.write_text(json.dumps(report_dict, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description=(
            "Run the fixed evaluation set against the deterministic mock provider and "
            "compare against the checked-in baseline. See docs/adr/0011-evaluation-and-"
            "regression-harness.md."
        ),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Explicitly overwrite the checked-in baseline with this run's result. Never run "
            "automatically in CI. Requires the evaluation to pass its own internal validity "
            "checks first. The resulting change must be reviewed like any other diff before "
            "committing."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the candidate report as JSON to this explicit path (not written by default).",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="Path to the evaluation fixture (default: tests/evaluation/fixed_cases.json).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Path to the baseline artifact (default: tests/evaluation/baseline.json).",
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=None,
        help=(
            "Path to the frozen retrieval calibration fixture used for the retrieval-policy "
            "section (default: tests/calibration/bge_similarity_calibration.json)."
        ),
    )
    parser.add_argument(
        "--adversarial-fixture",
        type=Path,
        default=None,
        help=(
            "Path to the adversarial-guardrail fixture (Section D; default: "
            "tests/evaluation/adversarial_cases.json)."
        ),
    )
    args = parser.parse_args(argv)

    fixture_path = args.fixture or DEFAULT_FIXTURE_PATH
    baseline_path = args.baseline or DEFAULT_BASELINE_PATH
    calibration_path = args.calibration or DEFAULT_CALIBRATION_PATH
    adversarial_fixture_path = args.adversarial_fixture or DEFAULT_ADVERSARIAL_FIXTURE_PATH
    command = "python -m app.evaluation" + (" --update-baseline" if args.update_baseline else "")

    try:
        fixture, fixture_hash = load_fixture(fixture_path)
    except Exception as exc:
        print(f"ERROR: could not load evaluation fixture: {exc}", file=sys.stderr)
        return 1

    try:
        retrieval_policy_fixture = load_retrieval_policy_fixture(calibration_path)
    except Exception as exc:
        print(
            f"ERROR: could not load retrieval-policy calibration evidence: {exc}", file=sys.stderr
        )
        return 1

    results = run_all(fixture.cases)
    incomplete = len(results) != len(fixture.cases)
    if incomplete:
        print(
            f"ERROR: case execution incomplete: {len(results)}/{len(fixture.cases)} cases ran.",
            file=sys.stderr,
        )
        return 1

    retrieval_policy_results = run_all_retrieval_policy_cases(retrieval_policy_fixture)
    if len(retrieval_policy_results) != len(retrieval_policy_fixture.cases):
        print(
            "ERROR: retrieval-policy case execution incomplete: "
            f"{len(retrieval_policy_results)}/{len(retrieval_policy_fixture.cases)} cases ran.",
            file=sys.stderr,
        )
        return 1

    try:
        adversarial_fixture, adversarial_fixture_hash = load_adversarial_fixture(
            adversarial_fixture_path
        )
    except Exception as exc:
        print(f"ERROR: could not load adversarial-guardrail fixture: {exc}", file=sys.stderr)
        return 1

    adversarial_results = run_all_adversarial_cases(adversarial_fixture)
    if len(adversarial_results) != len(adversarial_fixture.cases):
        print(
            "ERROR: adversarial-guardrail case execution incomplete: "
            f"{len(adversarial_results)}/{len(adversarial_fixture.cases)} cases ran.",
            file=sys.stderr,
        )
        return 1

    candidate = build_report(
        fixture,
        fixture_hash,
        results,
        retrieval_policy_fixture,
        retrieval_policy_results,
        adversarial_fixture,
        adversarial_fixture_hash,
        adversarial_results,
        command=command,
    )

    if args.output is not None:
        args.output.write_text(json.dumps(report_to_dict(candidate), indent=2), encoding="utf-8")

    if args.update_baseline:
        any_blocking = (
            any(not r.passed for r in results)
            or any(not r.passed for r in retrieval_policy_results)
            or any(not r.passed for r in adversarial_results)
        )
        if any_blocking:
            print(
                "ERROR: refusing to update the baseline -- the evaluation itself has blocking "
                "failures. Fix them first, then re-run --update-baseline.",
                file=sys.stderr,
            )
            print(format_human_summary(candidate, baseline=None, comparison=None))
            return 1
        _write_baseline(baseline_path, report_to_dict(candidate))
        print(
            f"Baseline UPDATED at {baseline_path}. This is a normal, reviewable git diff -- "
            "inspect it and get it reviewed before committing, exactly like any other change "
            "to checked-in evidence."
        )
        print()
        print(format_human_summary(candidate, baseline=None, comparison=None))
        return 0

    if not baseline_path.exists():
        print(
            f"ERROR: no baseline found at {baseline_path}. Run "
            "`python -m app.evaluation --update-baseline` first (after reviewing the result).",
            file=sys.stderr,
        )
        return 1

    baseline = _load_baseline(baseline_path)
    try:
        comparison = compare(baseline, candidate)
    except BaselineIncompatibleError as exc:
        print(f"ERROR: baseline comparison failed: {exc}", file=sys.stderr)
        return 1

    print(format_human_summary(candidate, baseline=baseline, comparison=comparison))

    return 0 if comparison.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
