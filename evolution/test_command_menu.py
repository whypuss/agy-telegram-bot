"""Menu/handler consistency + POLICY_ADMIN_IDS fail-closed.
Run: venv/bin/python3 -m evolution.test_command_menu
"""
import os
import subprocess
import sys
from types import SimpleNamespace

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


# The menu Telegram showed before /pin and /unpin existed.
ORIGINAL_MENU = ["usage", "model", "memory", "proposals", "compact",
                 "status", "reset", "cancel", "clear", "help"]


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def probe_admin_config(value) -> dict:
    """Load config in a clean interpreter with POLICY_ADMIN_IDS set to `value`.

    Runs from an empty temp cwd: config.py calls load_dotenv(), which walks up
    from the working directory, so clearing the env var alone would still pick
    up the repo's own .env and make "unset" untestable.
    """
    import json
    import tempfile

    env = dict(os.environ)
    env.pop("POLICY_ADMIN_IDS", None)
    env["PYTHONPATH"] = REPO
    env["ALLOWED_USER_IDS"] = "555000555"   # non-empty, so inheritance would show
    if value is not None:
        env["POLICY_ADMIN_IDS"] = value

    code = (
        "import json, config; "
        "print(json.dumps({'admins': sorted(config.POLICY_ADMIN_IDS), "
        "'allowed': sorted(config.ALLOWED_USER_IDS), "
        "'err': config.POLICY_ADMIN_CONFIG_ERROR, "
        "'admin_777': config.is_policy_admin(777), "
        "'admin_inherited': config.is_policy_admin(555000555)}))"
    )
    with tempfile.TemporaryDirectory(dir="/tmp") as clean:
        out = subprocess.run([sys.executable, "-c", code], env=env,
                             capture_output=True, text=True, cwd=clean)
    if not out.stdout.strip():
        raise RuntimeError(f"probe failed: {out.stderr[-400:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def main():
    import bot as botmod

    print("=== Menu / handler consistency ===")
    menu = botmod.build_command_menu()
    menu_names = [c.command for c in menu]

    collected = []
    botmod.register_handlers(SimpleNamespace(add_handler=collected.append))
    handler_names = set()
    for h in collected:
        handler_names |= {str(c) for c in (getattr(h, "commands", None) or set())}

    check("removed commands are absent from the menu",
          not ({"pin", "unpin"} & set(menu_names)), str(menu_names))
    check("every menu command has a registered handler",
          set(menu_names) <= handler_names, str(set(menu_names) - handler_names))
    check("original commands all still present",
          set(ORIGINAL_MENU) <= set(menu_names), str(set(ORIGINAL_MENU) - set(menu_names)))
    check("original menu order preserved",
          [n for n in menu_names if n in ORIGINAL_MENU] == ORIGINAL_MENU, str(menu_names))
    check("no duplicate menu entries", len(menu_names) == len(set(menu_names)))
    check("every menu name is its handler's first alias",
          all(aliases[0] in menu_names for aliases, _, d in botmod.COMMAND_SPEC if d))
    check("descriptions within Telegram's 256-char limit",
          all(1 <= len(c.description) <= 256 for c in menu))

    print("=== POLICY_ADMIN_IDS fail-closed ===")
    allowed = probe_admin_config("123")["allowed"]
    check("fixture: ALLOWED_USER_IDS is non-empty", bool(allowed), str(allowed))

    unset = probe_admin_config(None)
    check("unset -> empty, does NOT inherit ALLOWED_USER_IDS",
          unset["admins"] == [] and not unset["admin_inherited"], str(unset))
    check("unset -> warning recorded", bool(unset["err"]), str(unset))

    empty = probe_admin_config("   ")
    check("empty -> empty, no inheritance", empty["admins"] == [] and bool(empty["err"]), str(empty))

    bad = probe_admin_config("abc")
    check("malformed -> empty, no inheritance", bad["admins"] == [] and bool(bad["err"]), str(bad))

    partial = probe_admin_config("123,oops,456")
    check("one bad entry voids the whole allowlist (fail closed)",
          partial["admins"] == [] and "oops" in partial["err"], str(partial))

    good = probe_admin_config("123,456")
    check("valid config parses", good["admins"] == [123, 456] and not good["err"], str(good))

    denied = probe_admin_config("123")
    check("non-listed user denied", not denied["admin_777"], str(denied))

    print("\n" + (f"ALL PASS ({len(_results)})" if all(_results)
                  else f"FAILURES: {_results.count(False)}/{len(_results)}"))
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
