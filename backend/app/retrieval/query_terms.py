"""Deterministic meaningful-query validation.

Rejects empty, punctuation-only, or otherwise non-informative queries
before any embedding call or vector query runs — a small fixed stop-word
list and a minimum meaningful-term count, not an NLP dependency.
"""

from __future__ import annotations

import re

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
_MIN_TERM_LENGTH = 2
_MIN_MEANINGFUL_TERMS = 1

# A small, fixed set of common English function words — just enough to keep
# "the/a/of/is" style noise from counting as meaningful content. Not a
# general-purpose NLP stop-word list.
_STOP_WORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "of", "to", "in", "on", "for", "and", "or", "with", "at", "by",
        "this", "that", "these", "those", "it", "its", "as", "from",
    }
)  # fmt: skip


def extract_meaningful_terms(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, minus stop words and single-character
    noise. Deterministic and dependency-free."""
    tokens = _TOKEN_PATTERN.findall(text.lower())
    return [
        token for token in tokens if len(token) >= _MIN_TERM_LENGTH and token not in _STOP_WORDS
    ]


def has_sufficient_query_content(text: str) -> bool:
    """True when `text` carries at least one meaningful term. False for
    empty, whitespace-only, punctuation-only, or stop-word-only input."""
    return len(extract_meaningful_terms(text)) >= _MIN_MEANINGFUL_TERMS


# Corpus-generic terms: words that are "meaningful" (pass `extract_meaningful_
# terms`) but establish no subject-matter relevance in *this* corpus because
# nearly every chunk contains them. Measured by document frequency across the
# real demo corpus's 405 retrieval_chunks (100 pallets/flask issues + 6 Flask
# documentation excerpts; measured 2026-09-15): every term appearing in >=10%
# of chunks is included here. That cutoff falls in a real gap in the measured
# distribution — code-syntax/boilerplate/issue-template tokens (flask 53.6%,
# app 26.2%, python 23.7%, request 17.5%, file 14.3%, code 11.4%,
# import/def/return 11.1% each, http/when 10.9%, environment/version 10.6%,
# run/site 10.4%, response 10.1%, ...) cluster at or above it, while genuine
# topical words measured for comparison sit well below it (session 5.2%,
# security 4.0%, redirect 4.4%, endpoint 3.7%, protection 1.5%, cookie 0.5%)
# — so the cutoff is not close to accidentally excluding real subject-matter
# terms. ("environment"/"version" earned their place here the hard way: an
# earlier, incomplete version of this list left them out despite measuring
# above the cutoff, and live verification against the real demo corpus
# showed issue #5804 being accepted for issue #5755 on nothing but a shared
# "environment, version" — both boilerplate from the standard bug-report
# template every issue in this corpus uses, not genuine topical overlap.)
# "issue" and "project" are added even though their raw body-text frequency
# happens to fall under 10% (issues rarely spell out the word "issue" to
# describe themselves): every chunk in this corpus IS an issue in a project,
# so by construction those two words carry zero discriminative value here
# regardless of surface frequency.
_GENERIC_TERMS = frozenset(
    {
        "flask", "app", "python", "not", "py", "request", "if", "test",
        "file", "https", "name", "com", "gt", "get", "code", "context",
        "def", "import", "return", "http", "when", "environment", "version",
        "run", "site", "response", "issue", "project",
    }
)  # fmt: skip

# General low-content English words: personal pronouns, modal verbs, and
# common hedge/filler adverbs. Unlike `_GENERIC_TERMS` above, this set is NOT
# derived from this corpus's document frequency — some of these words are
# individually rare enough in the demo corpus to slip under any reasonable
# frequency cutoff (e.g. "using" measured at only 5.7%) while still carrying
# no discriminative concept on their own, in any corpus. (Caught live: issue
# #5836, "Test failures with click 8.3.1", was briefly accepted for #5755,
# "Flask cannot find /security/logic API", on nothing but a shared "using".)
# Bounded and reviewed by hand, not per-instance patched: these are standard,
# closed grammatical categories (pronouns, modals, hedges), not individual
# word exceptions.
_LOW_CONTENT_TERMS = frozenset(
    {
        # personal pronouns
        "i", "we", "you", "they", "he", "she", "us", "them",
        "our", "your", "my", "his", "her", "their",
        # modal verbs
        "can", "could", "would", "should", "will", "might", "may", "must",
        # hedge / filler adverbs
        "also", "well", "just", "only", "still", "even", "really",
        "actually", "no", "but", "one", "much", "many",
        # "to use", every inflected form — a verb about *doing something
        # with* a tool, not the subject matter itself
        "use", "used", "using", "uses",
    }
)  # fmt: skip


def extract_discriminative_terms(text: str) -> list[str]:
    """`extract_meaningful_terms`, further stripped of corpus-generic terms
    (`_GENERIC_TERMS`), general low-content English words (`_LOW_CONTENT_
    TERMS`), and standalone numeric tokens (version numbers, counts — rarely
    meaningful on their own). Used only to decide whether a medium-confidence
    retrieval candidate has genuine lexical support (see
    `app.retrieval.service`); the broader `extract_meaningful_terms` is still
    what gates whether a query has *any* content at all."""
    return [
        term
        for term in extract_meaningful_terms(text)
        if term not in _GENERIC_TERMS and term not in _LOW_CONTENT_TERMS and not term.isdigit()
    ]
