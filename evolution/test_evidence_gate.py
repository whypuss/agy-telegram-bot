"""Acceptance matrix for the evidence gate. Run: venv/bin/python3 -m evolution.test_evidence_gate"""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from evolution.evidence import check_evidence
from evolution.schema import EvolutionCandidate
from evolution import gate

BEHAVIOR = "gateway restart handler returns 200 instead of dropping the connection"

MATRIX = [
    ("missing evidence",            None,                                                   False, "missing_evidence"),
    ("empty evidence",              "   ",                                                  False, "missing_evidence"),
    ("subjective claim only",       "Confirmed working, the gateway restart looks fine now.", False, "subjective_only"),
    ("exit code only",              "Ran the restart command, exit code 0, no errors.",      False, "exit_code_only"),
    ("generic check only",          "Lint passed and the gateway module imports OK.",        False, "generic_check_only"),
    ("concrete but unrelated",      "pytest tests/test_billing.py -> 14 passed in 2.31s",    False, "unrelated_evidence"),
    ("concrete + exercises target", "curl -s -o /dev/null -w '%{http_code}' localhost:8080/gateway/restart -> 200 (was: connection reset)", True, "ok"),
]


def run_matrix() -> bool:
    print("=== Evidence matrix ===")
    ok = True
    for name, ev, want_ok, want_code in MATRIX:
        v = check_evidence(ev, BEHAVIOR)
        good = (v.ok == want_ok) and (v.code == want_code)
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {name:<30} -> {v.code} (want {want_code})")
    return ok


def run_structural() -> bool:
    """Rejected candidates must not reach disk; accepted ones must be schema-valid and unpinned."""
    print("=== Structural ===")
    ok = True
    tmp = Path(tempfile.mkdtemp())
    orig_prop, orig_pol = gate.PROPOSALS_DIR, gate.POLICIES_DIR
    gate.PROPOSALS_DIR, gate.POLICIES_DIR = tmp / "proposals", tmp / "policies"
    gate.PROPOSALS_DIR.mkdir(parents=True)
    gate.POLICIES_DIR.mkdir(parents=True)
    try:
        def cand(ev, behavior, pinned=False):
            return EvolutionCandidate(
                candidate_type="policy_proposal", scope="permanent",
                rule_id="test-rule", summary="Verify the gateway restart path before reporting success.",
                root_cause="Reported done on edit success.", confidence=0.95,
                evidence=ev, affected_behavior=behavior, pinned=pinned,
            )

        rejected = asyncio.run(gate.dispatch_candidate(cand("exit code 0", BEHAVIOR)))
        wrote = list(gate.PROPOSALS_DIR.glob("*.json"))
        good = rejected is None and not wrote
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  rejected candidate writes nothing ({len(wrote)} files)")

        # An accepted candidate that ASKS to be pinned must still come out unpinned.
        pid = asyncio.run(gate.dispatch_candidate(cand(MATRIX[-1][1], BEHAVIOR, pinned=True)))
        good = pid is not None
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  accepted candidate creates proposal")

        if pid:
            prop = json.loads((gate.PROPOSALS_DIR / f"{pid}.json").read_text())
            good = prop["pinned"] is False
            ok &= good
            print(f"  {'PASS' if good else 'FAIL'}  evaluator-requested pinning stripped (pinned={prop['pinned']})")

            succeeded, msg = gate.approve_proposal(pid, approved_by=1)
            pols = list(gate.POLICIES_DIR.glob("*.json"))
            good = succeeded and len(pols) == 1
            ok &= good
            print(f"  {'PASS' if good else 'FAIL'}  compiles to policy ({msg.strip()[:40]})")

            if pols:
                p = json.loads(pols[0].read_text())
                required = {"id", "summary", "root_cause", "evidence", "pinned",
                            "version", "created_at", "last_updated", "last_used_at", "status"}
                good = required <= set(p)
                ok &= good
                print(f"  {'PASS' if good else 'FAIL'}  policy matches VersionedPolicy schema")

                good = p["pinned"] is False and p["status"] == "active"
                ok &= good
                print(f"  {'PASS' if good else 'FAIL'}  compiled policy is active, NOT pinned")

                good = p["evidence"] == MATRIX[-1][1]
                ok &= good
                print(f"  {'PASS' if good else 'FAIL'}  evidence preserved byte-for-byte")

        # A proposal hand-edited to strip its evidence must not compile.
        pid2 = asyncio.run(gate.dispatch_candidate(cand(MATRIX[-1][1], BEHAVIOR)))
        pp = gate.PROPOSALS_DIR / f"{pid2}.json"
        d = json.loads(pp.read_text()); d["evidence"] = ""
        pp.write_text(json.dumps(d, ensure_ascii=False))
        succeeded, msg = gate.approve_proposal(pid2, approved_by=1)
        good = not succeeded
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  compiler refuses evidence-stripped proposal")
        return ok
    finally:
        gate.PROPOSALS_DIR, gate.POLICIES_DIR = orig_prop, orig_pol
        shutil.rmtree(tmp, ignore_errors=True)


def run_structured_fields() -> bool:
    """Trigger/Constraint/Verification render when present, and are optional."""
    print("=== Structured fields ===")
    ok = True
    tmp = Path(tempfile.mkdtemp())
    from evolution import lifecycle
    import memory_manager
    orig = lifecycle.POLICIES_DIR
    lifecycle.POLICIES_DIR = tmp / "policies"
    lifecycle.POLICIES_DIR.mkdir(parents=True)
    try:
        base = {"id": "rule_x", "summary": "Check before restart.", "root_cause": "Blind restart.",
                "evidence": "systemctl status -> inactive", "pinned": False, "version": 1,
                "created_at": 1, "last_updated": 1, "last_used_at": 1, "status": "active"}

        # Without the optional fields — must render as a bare single line.
        (lifecycle.POLICIES_DIR / "rule_x.json").write_text(json.dumps(base), encoding="utf-8")
        plain = memory_manager.build_memory_context()
        good = "Check before restart." in plain and "‣ 觸發" not in plain
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  policy without structured fields renders unchanged")

        # With them — must expand into labelled sub-lines.
        rich = dict(base, trigger="Before restarting any production service",
                    constraint="Capture current status first",
                    verification="Re-run status and compare")
        (lifecycle.POLICIES_DIR / "rule_x.json").write_text(json.dumps(rich), encoding="utf-8")
        out = memory_manager.build_memory_context()
        good = all(f"‣ {lbl}: " in out for lbl in ("觸發", "約束", "驗證"))
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  structured fields render as labelled lines")

        # Partial population must not emit empty labels.
        partial = dict(base, verification="Re-run status and compare")
        (lifecycle.POLICIES_DIR / "rule_x.json").write_text(json.dumps(partial), encoding="utf-8")
        out = memory_manager.build_memory_context()
        good = "‣ 驗證: " in out and "‣ 觸發" not in out and "‣ 約束" not in out
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  partial fields emit only what is set")
        return ok
    finally:
        lifecycle.POLICIES_DIR = orig
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    a, b = run_matrix(), run_structural()
    b = b and run_structured_fields()
    print("\n" + ("ALL PASS" if a and b else "FAILURES PRESENT"))
    raise SystemExit(0 if a and b else 1)
