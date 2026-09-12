"""Acceptance matrix for /pin and /unpin. Run: venv/bin/python3 -m evolution.test_pin_command"""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from evolution import gate, lifecycle

# Fictional ids. The real admin list lives in .env and is never checked in —
# this repo is public, and a Telegram user id is a personal identifier.
ADMIN, OUTSIDER = 100000001, 999000111
IMMUTABLE = ("summary", "root_cause", "evidence", "version", "created_at", "id")

_results = []


def check(name, cond):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def make_policy(path: Path, pid: str, status="active", pinned=False, idle_days=0):
    now = int(__import__("time").time())
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


async def fake_call(handler, uid, args, sent: list):
    """Drive the real handler with a minimal Update/Context double."""
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        message=SimpleNamespace(message_id=1),
    )
    context = SimpleNamespace(args=args)
    import bot as botmod
    orig = botmod.send_formatted_reply

    async def capture(update, context, text, reply_to_message_id=None, **kw):
        sent.append(text)
    botmod.send_formatted_reply = capture
    try:
        await handler(update, context)
    finally:
        botmod.send_formatted_reply = orig


def main():
    tmp = Path(tempfile.mkdtemp())
    orig_pol, orig_life = gate.POLICIES_DIR, lifecycle.POLICIES_DIR
    gate.POLICIES_DIR = lifecycle.POLICIES_DIR = tmp / "policies"
    gate.POLICIES_DIR.mkdir(parents=True)
    lifecycle.ARCHIVE_DIR = tmp / "policies/.archive"
    lifecycle.ARCHIVE_DIR.mkdir(parents=True)

    import bot as botmod
    import config as configmod
    # Test against fixtures, not the real .env. Both gates matter: cmd_pin checks
    # is_authorized() before is_policy_admin(), so patching only the admin set
    # would make every case fail at the outer gate.
    orig_admins, orig_allowed = configmod.POLICY_ADMIN_IDS, configmod.ALLOWED_USER_IDS
    configmod.POLICY_ADMIN_IDS = {ADMIN}
    configmod.ALLOWED_USER_IDS = {ADMIN, OUTSIDER}
    try:
        active = gate.POLICIES_DIR / "rule_active.json"
        make_policy(active, "rule_active", idle_days=45)          # stale-eligible
        make_policy(gate.POLICIES_DIR / "rule_arch.json", "rule_arch", status="archived")
        before = json.loads(active.read_text())

        print("=== Authorization ===")
        sent = []
        asyncio.run(fake_call(botmod.cmd_pin, OUTSIDER, ["rule_active"], sent))
        unchanged = json.loads(active.read_text()) == before
        check("unauthorized user rejected, file unchanged", unchanged and not json.loads(active.read_text())["pinned"])

        print("=== Argument handling ===")
        sent = []
        asyncio.run(fake_call(botmod.cmd_pin, ADMIN, [], sent))
        check("missing rule ID -> usage error", sent and "用法" in sent[0])

        sent = []
        asyncio.run(fake_call(botmod.cmd_pin, ADMIN, ["rule_nope"], sent))
        check("unknown rule ID rejected", sent and "找不到" in sent[0])

        sent = []
        asyncio.run(fake_call(botmod.cmd_pin, ADMIN, ["p_1789_test-rule"], sent))
        check("proposal ID rejected", sent and "找不到" in sent[0])

        ok, msg = gate.set_policy_pinned("../../../etc/passwd", True, actor=ADMIN)
        check("path traversal rejected", not ok and "無效" in msg)

        print("=== Status guard ===")
        sent = []
        asyncio.run(fake_call(botmod.cmd_pin, ADMIN, ["rule_arch"], sent))
        arch = json.loads((gate.POLICIES_DIR / "rule_arch.json").read_text())
        check("archived policy rejected, still unpinned", sent and "archived" in sent[0] and not arch["pinned"])

        print("=== Pin / unpin ===")
        sent = []
        asyncio.run(fake_call(botmod.cmd_pin, ADMIN, ["rule_active"], sent))
        d = json.loads(active.read_text())
        check("authorized pin succeeds", d["pinned"] is True)
        check("message reports id, transition, version, path",
              sent and all(t in sent[0] for t in ("rule_active", "`False` → `True`", "version", str(active))))
        check("version not bumped", d["version"] == before["version"])
        check("immutable fields untouched", all(d[k] == before[k] for k in IMMUTABLE))

        sent = []
        asyncio.run(fake_call(botmod.cmd_pin, ADMIN, ["rule_active"], sent))
        d2 = json.loads(active.read_text())
        check("already pinned -> no-op", sent and "已經係 pinned" in sent[0] and d2 == d)

        print("=== Lifecycle interaction ===")
        stats = lifecycle.run_lifecycle_pass()
        d3 = json.loads(active.read_text())
        check("pinned policy exempt from staleness",
              stats["skipped_pinned"] >= 1 and d3["status"] == "active")

        sent = []
        asyncio.run(fake_call(botmod.cmd_unpin, ADMIN, ["rule_active"], sent))
        d4 = json.loads(active.read_text())
        check("authorized unpin succeeds", d4["pinned"] is False)
        check("unpin leaves immutable fields alone", all(d4[k] == before[k] for k in IMMUTABLE))
        check("policy not deleted", active.exists())

        sent = []
        asyncio.run(fake_call(botmod.cmd_unpin, ADMIN, ["rule_active"], sent))
        check("already unpinned -> no-op", sent and "本來就唔係 pinned" in sent[0])

        stats2 = lifecycle.run_lifecycle_pass()
        d5 = json.loads(active.read_text())
        check("after unpin, lifecycle applies again",
              stats2["skipped_pinned"] == 0 and d5["status"] == "stale")

        print("=== Handler registration ===")
        # Asserts against the same register_handlers() main() calls, not a copy.
        collected = []
        botmod.register_handlers(SimpleNamespace(add_handler=collected.append))
        names = set()
        for h in collected:
            names |= {str(c) for c in (getattr(h, "commands", None) or set())}
        check("/pin and /unpin registered", {"pin", "unpin"} <= names)
        check("existing handlers still registered",
              {"start", "approve", "reject", "help"} <= names and len(collected) > 20)
    finally:
        gate.POLICIES_DIR, lifecycle.POLICIES_DIR = orig_pol, orig_life
        configmod.POLICY_ADMIN_IDS = orig_admins
        configmod.ALLOWED_USER_IDS = orig_allowed
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + (f"ALL PASS ({len(_results)})" if all(_results)
                  else f"FAILURES: {_results.count(False)}/{len(_results)}"))
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
