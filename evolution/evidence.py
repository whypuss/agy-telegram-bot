"""
Deterministic Evidence Gate.

Invariant:
    No candidate may be promoted unless its evidence records an actual
    verification that exercises the affected behavior; successful execution
    alone is insufficient.

Pure functions, no LLM. The evaluator that produces candidates is itself a
model, so the gate that judges them must not be — otherwise a hallucinated
candidate is graded by the same faculty that hallucinated it.

Scope: promotion only (policy_proposal / skill_patch). user_profile and
system_facts take the low-risk auto-commit path and are not gated here.
"""
import re
from dataclasses import dataclass
from typing import Optional, Set

MIN_EVIDENCE_CHARS = 12
MIN_BEHAVIOR_CHARS = 8

# Wording that asserts an outcome without recording one.
_SUBJECTIVE = [
    r"應該", r"睇落", r"似乎", r"估計", r"應[該当]?冇問題", r"搞掂", r"冇問題",
    r"\bshould\b", r"\bseems?\b", r"\blooks? (?:good|fine|ok)\b", r"\bprobably\b",
    r"\bworks? now\b", r"\bfixed\b", r"\bconfirmed\b", r"\bverified\b",
    r"\bsuccessfully\b", r"\bas expected\b",
]

# A command completing is not a behavioral result.
_EXIT_CODE_ONLY = [
    r"\bexit(?:\s*|-)?code\s*[:=]?\s*0\b", r"\breturned\s+0\b", r"\bexit\s+0\b",
    r"\bno errors?\b", r"\bcommand (?:succeeded|completed)\b",
    r"退出碼\s*0", r"返回\s*0", r"執行成功", r"命令成功",
]

# Checks that only prove their own scope.
_GENERIC_CHECK = [
    r"\blint(?:ing|er)?\b", r"\bimports? (?:ok|succeed|success|fine|worked)\b",
    r"\bjson\.?(?:parse|loads?)\b", r"\bparsed? (?:ok|successfully)\b",
    r"\bsyntax (?:ok|valid)\b", r"\bcompiles?\b", r"\btype[- ]?check\b",
    r"語法正確", r"格式正確", r"匯入成功", r"导入成功",
]

# Traces of something actually having run and produced output.
_CONCRETE = [
    r"\bTraceback\b", r"\b[A-Za-z_]*(?:Error|Exception)\b", r"\berror:", r"\bassert",
    r"\b\d+\s*(?:passed|failed|tests?|rows?|lines?|bytes?|ms|s)\b",
    r"\b(?:PASS|FAIL|OK)\b", r"\bHTTP/\d", r"\b[45]\d{2}\b",
    r"/[\w.\-/]+\.\w+",              # a file path
    r"\b\w+\.\w+:\d+\b",             # file:line
    r"^\s*[$>#]\s*\S",               # a shell prompt with a command
    r"`[^`]{3,}`",                   # a quoted snippet
    r"\b\d{2,}\b",                   # a measured quantity
    r"通過\s*\d+", r"失敗\s*\d+",
]

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "was",
    "were", "are", "not", "but", "its", "it's", "when", "then", "than", "into",
    "after", "before", "does", "did", "done", "will", "can", "should", "would",
    "run", "ran", "runs", "output", "result", "results", "check", "checked",
    "test", "tests", "tested", "code", "file", "files", "line", "lines",
}

_CJK = r"一-鿿"


@dataclass
class EvidenceVerdict:
    ok: bool
    code: str
    reason: str


def _matches(patterns, text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE | re.MULTILINE) for p in patterns)


def _tokens(text: str) -> Set[str]:
    """Latin words >=3 chars plus CJK character bigrams, minus stopwords."""
    low = text.lower()
    words = {w for w in re.findall(r"[a-z][a-z0-9_\-]{2,}", low) if w not in _STOPWORDS}
    for run in re.findall(f"[{_CJK}]{{2,}}", text):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words


def check_evidence(
    evidence: Optional[str],
    affected_behavior: Optional[str],
) -> EvidenceVerdict:
    """Judge whether evidence records a verification of the affected behavior."""
    behavior = (affected_behavior or "").strip()
    if len(behavior) < MIN_BEHAVIOR_CHARS:
        return EvidenceVerdict(
            False, "no_affected_behavior",
            "Candidate does not state which behavior it affects, so no evidence can be tied to it."
        )

    ev = (evidence or "").strip()
    if not ev:
        return EvidenceVerdict(False, "missing_evidence", "Candidate carries no evidence.")
    if len(ev) < MIN_EVIDENCE_CHARS:
        return EvidenceVerdict(
            False, "empty_evidence",
            f"Evidence is {len(ev)} chars — too short to record a verification."
        )

    def _survives(patterns) -> bool:
        """Is there still a concrete trace once these patterns are removed?"""
        rest = ev
        for p in patterns:
            rest = re.sub(p, " ", rest, flags=re.IGNORECASE)
        return _matches(_CONCRETE, rest)

    # The specific rejections are checked before the generic ones: the reason
    # returned here is what gets logged and shown on refusal, so "you cited an
    # exit code" must not be flattened into "no verification found".

    # A zero exit code is the classic false positive — the command can succeed
    # without ever exercising the behavior under test.
    if _matches(_EXIT_CODE_ONLY, ev) and not _survives(_EXIT_CODE_ONLY):
        return EvidenceVerdict(
            False, "exit_code_only",
            "Evidence is a successful exit with no behavioral result attached."
        )

    # A generic check proves only its own scope, unless that scope *is* the
    # affected behavior (a lint rule change may legitimately cite lint output).
    if (_matches(_GENERIC_CHECK, ev) and not _matches(_GENERIC_CHECK, behavior)
            and not _survives(_GENERIC_CHECK)):
        return EvidenceVerdict(
            False, "generic_check_only",
            "Evidence is a generic check (lint/import/parse) that does not exercise the affected behavior."
        )

    if not _matches(_CONCRETE, ev):
        if _matches(_SUBJECTIVE, ev):
            return EvidenceVerdict(
                False, "subjective_only",
                "Evidence asserts an outcome but records none — no tool output, test result, or artifact."
            )
        return EvidenceVerdict(
            False, "no_verification",
            "Evidence contains no trace of an actual verification."
        )

    # Lexical relatedness. This is a proxy, not comprehension: it catches
    # evidence pasted from an unrelated run, not evidence that is subtly off.
    shared = _tokens(behavior) & _tokens(ev)
    if not shared:
        return EvidenceVerdict(
            False, "unrelated_evidence",
            "Evidence shares no term with the affected behavior — it does not appear to exercise it."
        )

    return EvidenceVerdict(
        True, "ok",
        f"Evidence records a verification touching the affected behavior ({', '.join(sorted(shared)[:3])})."
    )
