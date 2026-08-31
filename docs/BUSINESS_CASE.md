# RepoTriage AI Business Case

## Verified problem

Growing software companies, developer-tool teams, and open-source maintainers receive duplicate, incomplete, misclassified, repetitive, and misrouted GitHub issues. Senior engineers and maintainers must repeatedly interpret reports, search repository documentation and resolved issues, estimate priority, determine actionability, and prepare a response.

## Target organizations and users

The intended organization is a small-to-mid-sized B2B software or developer-tools team with multiple repositories, limited support and engineering capacity, and an incoming issue volume that exceeds senior-review capacity.

Primary users are support engineers, developer-relations teams, engineering managers, QA leads, product-operations teams, and open-source maintainers.

## Governed recommendation workflow

RepoTriage AI will import a bounded public issue set, retrieve scoped repository evidence, and propose an auditable triage recommendation. The planned workflow is `classify → retrieve → assess → propose → human review`.

Evidence, system inference, proposed action, and human decision remain distinct. AI output is proposed, never approved: an authorized human must make every final decision. The system must not automatically comment on, close, modify, or approve third-party repository content.

## Differentiation

The project is not a generic label bot or RAG chatbot. Its planned differentiation is evidence-backed recommendations with source links, human approval, repository and tenant isolation, provider-neutral routing and fallback, traceable prompts and model use, evaluation and regression controls, and append-only auditability.

## Measurable outcomes

The project will measure classification quality, retrieval hit rate or Recall@K, duplicate-detection precision and Recall@K where known pairs exist, groundedness, citation correctness, review usefulness, workflow reliability, estimated token cost, demo-environment latency, and security-denial results.

## Unproven assumptions and claim boundary

The project does not yet prove customer ROI, enterprise-scale performance, production load behavior, or outcomes for real customers. Retrospective public-data evaluation is not live enterprise ROI and must never be represented as such.

## Evidence Base

The documented research themes are issue-triage burden, duplicate and incomplete reports, repository evidence retrieval, human-governed AI recommendations, and evaluation of classification, retrieval, and safety behavior. The authoritative control documents contain no approved external source URLs or statistics. Adding exact, reviewed external source links is a follow-up documentation task.