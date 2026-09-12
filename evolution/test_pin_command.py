"""Acceptance matrix for set_policy_pinned().

The /pin and /unpin Telegram commands were removed. Pinning is now a
maintenance operation callable from Python only, and set_policy_pinned()
remains the single read/write path for a policy's pinned flag — so its
guarantees (status guard, idempotence, field immutability, lifecycle exemption)
still need covering.

Run: venv/bin/python3 -m evolution.test_pin_command
"""
import json
import shutil
import tempfile
import time
from pathlib import Path

from evolution import gate, lifecycle

ACTOR = 100000001          # fictional; the real admin list is never checked in
IMMUTABLE = ("summary", "root_cause", "evidence", "version", "created_at", "id")

_results = []


def check(name, cond):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def make_policy(path: Path, pid: str, status="active", pinned=False, idle_days=0):
    now = int(time.time())
    path.write_text(json.dumps({
        "id": pid, "summary": "Verify before reporting success.",
        "root_cause": "Reported done on edit success.",
        "evidence": "curl -> 200 (was: connection reset)",
        "pinned": pinned, "version": 1,
        "created_at": now - idle_days * 86400,
        "last_updated": now - idle_days * 86400,
        "last_used_at": now - idle_days * 86400,
        "status": status,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp())
    orig_pol, orig_life, orig_arch = gate.POLICIES_DIR, lifecycle.POLICIES_DIR, lifecycle.ARCHIVE_DIR
    gate.POLICIES_DIR = lifecycle.POLICIES_DIR = tmp / "policies"
    gate.POLICIES_DIR.mkdir(parents=True)
    lifecycle.ARCHIVE_DIR = tmp / "policies/.archive"
    lifecycle.ARCHIVE_DIR.mkdir(parents=True)
    try:
        active = gate.POLICIES_DIR / "rule_active.json"
        make_policy(active, "rule_active", idle_days=45)          # stale-eligible
        make_policy(gate.POLICIES_DIR / "rule_arch.json", "rule_arch", status="archived")
        before = json.loads(active.read_text())

        print("=== Input validation ===")
        ok, msg = gate.set_policy_pinned("", True, actor=ACTOR)
        check("empty rule ID rejected", not ok and "無效" in msg)

        ok, msg = gate.set_policy_pinned("一定要用繁體中文回答我", True, actor=ACTOR)
        check("natural language rejected, never treated as rule content",
              not ok and "無效" in msg)

        ok, msg = gate.set_policy_pinned("../../../etc/passwd", True, actor=ACTOR)
        check("path traversal rejected", not ok and "無效" in msg)

        ok, msg = gate.set_policy_pinned("rule_nope", True, actor=ACTOR)
        check("unknown rule ID rejected", not ok and "找不到" in msg)

        ok, msg = gate.set_policy_pinned("p_1789_test-rule", True, actor=ACTOR)
        check("proposal ID rejected", not ok and "找不到" in msg)

        print("=== Status guard ===")
        ok, msg = gate.set_policy_pinned("rule_arch", True, actor=ACTOR)
        arch = json.loads((gate.POLICIES_DIR / "rule_arch.json").read_text())
        check("archived policy rejected, still unpinned",
              not ok and "archived" in msg and not arch["pinned"])

        print("=== Pin / unpin ===")
        ok, msg = gate.set_policy_pinned("rule_active", True, actor=ACTOR)
        d = json.loads(active.read_text())
        check("pin succeeds", ok and d["pinned"] is True)
        check("message reports id, transition, version, path",
              all(t in msg for t in ("rule_active", "`False` → `True`", "version", str(active))))
        check("version not bumped", d["version"] == before["version"])
        check("immutable fields untouched", all(d[k] == before[k] for k in IMMUTABLE))

        ok, msg = gate.set_policy_pinned("rule_active", True, actor=ACTOR)
        d2 = json.loads(active.read_text())
        check("already pinned -> no-op, zero writes", ok and "已經係 pinned" in msg and d2 == d)

        print("=== Lifecycle interaction ===")
        stats = lifecycle.run_lifecycle_pass()
        d3 = json.loads(active.read_text())
        check("pinned policy exempt from staleness",
              stats["skipped_pinned"] >= 1 and d3["status"] == "active")

        ok, msg = gate.set_policy_pinned("rule_active", False, actor=ACTOR)
        d4 = json.loads(active.read_text())
        check("unpin succeeds", ok and d4["pinned"] is False)
        check("unpin leaves immutable fields alone", all(d4[k] == before[k] for k in IMMUTABLE))
        check("policy not deleted", active.exists())

        ok, msg = gate.set_policy_pinned("rule_active", False, actor=ACTOR)
        check("already unpinned -> no-op", ok and "本來就唔係 pinned" in msg)

        stats2 = lifecycle.run_lifecycle_pass()
        d5 = json.loads(active.read_text())
        check("after unpin, lifecycle applies again",
              stats2["skipped_pinned"] == 0 and d5["status"] == "stale")

        print("=== Command surface removed ===")
        import bot as botmod
        check("no cmd_pin / cmd_unpin handlers remain",
              not hasattr(botmod, "cmd_pin") and not hasattr(botmod, "cmd_unpin"))
    finally:
        gate.POLICIES_DIR, lifecycle.POLICIES_DIR, lifecycle.ARCHIVE_DIR = orig_pol, orig_life, orig_arch
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + (f"ALL PASS ({len(_results)})" if all(_results)
                  else f"FAILURES: {_results.count(False)}/{len(_results)}"))
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
