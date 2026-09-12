"""Menu / handler consistency. Run: venv/bin/python3 -m test_command_menu"""
from types import SimpleNamespace

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


EXPECTED = ["usage", "model", "memory", "compact", "status", "reset", "cancel", "clear", "help"]
REMOVED = {"pin", "unpin", "proposals", "approve", "reject"}


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

    check("every menu command has a registered handler",
          set(menu_names) <= handler_names, str(set(menu_names) - handler_names))
    check("expected commands are all present",
          set(EXPECTED) <= set(menu_names), str(set(EXPECTED) - set(menu_names)))
    check("menu order is stable",
          [n for n in menu_names if n in EXPECTED] == EXPECTED, str(menu_names))
    check("no duplicate menu entries", len(menu_names) == len(set(menu_names)))
    check("every menu name is its handler's first alias",
          all(aliases[0] in menu_names for aliases, _, d in botmod.COMMAND_SPEC if d))
    check("descriptions within Telegram's 256-char limit",
          all(1 <= len(c.description) <= 256 for c in menu))

    print("=== Removed commands stay removed ===")
    check("not in the menu", not (REMOVED & set(menu_names)), str(REMOVED & set(menu_names)))
    check("no handlers registered", not (REMOVED & handler_names), str(REMOVED & handler_names))
    src = open(botmod.__file__, encoding="utf-8").read()
    check("not advertised in /help", "/proposals" not in src and "/pin" not in src)

    print("\n" + (f"ALL PASS ({len(_results)})" if all(_results)
                  else f"FAILURES: {_results.count(False)}/{len(_results)}"))
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
