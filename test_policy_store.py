"""Acceptance matrix for policy injection. Run: venv/bin/python3 -m test_policy_store"""
import json
import shutil
import tempfile
from pathlib import Path

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


BASE = {"id": "rule_x", "summary": "Check before restart.", "root_cause": "Blind restart.",
        "evidence": "systemctl status -> inactive", "version": 1,
        "created_at": 1, "last_updated": 1, "status": "active"}


def main():
    import policy_store
    import memory_manager

    tmp = Path(tempfile.mkdtemp())
    orig = policy_store.POLICIES_DIR
    policy_store.POLICIES_DIR = tmp / "policies"
    policy_store.POLICIES_DIR.mkdir(parents=True)
    try:
        def write(d):
            (policy_store.POLICIES_DIR / "rule_x.json").write_text(
                json.dumps(d, ensure_ascii=False), encoding="utf-8")

        def read():
            return json.loads((policy_store.POLICIES_DIR / "rule_x.json").read_text())

        print("=== Injection ===")
        write(BASE)
        out = memory_manager.build_memory_context()
        check("active policy reaches the prompt", "Check before restart." in out)
        check("it lands under the hardline heading", "HARDLINE BEHAVIOR POLICIES" in out)
        check("no marker for a property that no longer exists", "[PINNED]" not in out)

        print("=== Structured fields are optional ===")
        check("bare policy renders as one line", "‣ 觸發" not in out)
        write(dict(BASE, trigger="Before restarting any production service",
                   constraint="Capture current status first",
                   verification="Re-run status and compare"))
        out = memory_manager.build_memory_context()
        check("present fields render as labelled lines",
              all(f"‣ {lbl}: " in out for lbl in ("觸發", "約束", "驗證")))
        write(dict(BASE, verification="Re-run status and compare"))
        out = memory_manager.build_memory_context()
        check("partial fields emit only what is set",
              "‣ 驗證: " in out and "‣ 觸發" not in out and "‣ 約束" not in out)

        print("=== Injection is not usage ===")
        write(dict(BASE, last_used_at=1000000))
        memory_manager.build_memory_context()
        d = read()
        check("injection does not advance last_used_at", d["last_used_at"] == 1000000)
        check("injection is recorded separately", d.get("last_injected_at", 0) > 1000000)
        check("no automatic ageing exists",
              not any(hasattr(policy_store, a) for a in
                      ("run_lifecycle_pass", "ARCHIVE_DIR", "STALE_DAYS")))

        print("=== Manual control is the only lifecycle ===")
        write(BASE)
        check("active policy is listed", len(policy_store.get_active_policies()) == 1)
        ok, _ = policy_store.set_policy_status("rule_x", "disabled")
        check("disabling stops injection", ok and len(policy_store.get_active_policies()) == 0)
        ok, msg = policy_store.set_policy_status("rule_x", "disabled")
        check("repeat is a no-op", ok and "已經係" in msg)
        ok, _ = policy_store.set_policy_status("rule_x", "bogus")
        check("invalid status rejected", not ok)
        ok, _ = policy_store.set_policy_status("rule_nope", "active")
        check("unknown rule rejected", not ok)
        d = read()
        check("status change touches nothing else",
              d["summary"] == BASE["summary"] and d["version"] == 1 and d["created_at"] == 1)

        print("=== Failure is loud ===")
        (policy_store.POLICIES_DIR / "rule_broken.json").write_text("{ not json")
        before = len(policy_store.get_active_policies())
        check("a corrupt policy is skipped, not fatal", isinstance(before, int))
        src = open(policy_store.__file__, encoding="utf-8").read()
        check("and it is logged as not in effect", "POLICY NOT LOADED" in src)
        src = open(memory_manager.__file__, encoding="utf-8").read()
        check("losing all policies is logged", "HARDLINE POLICIES NOT INJECTED" in src)

        print("=== The pipeline is gone ===")
        for mod in ("evolution", "evolution.gate", "evolution.evidence", "evolution.queue"):
            try:
                __import__(mod)
                check(f"{mod} is importable", False)
            except ImportError:
                check(f"{mod} no longer exists", True)
    finally:
        policy_store.POLICIES_DIR = orig
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + (f"ALL PASS ({len(_results)})" if all(_results)
                  else f"FAILURES: {_results.count(False)}/{len(_results)}"))
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
