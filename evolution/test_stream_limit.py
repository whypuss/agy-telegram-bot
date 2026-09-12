"""Regression for the NDJSON stream truncation that made /compact return raw
protocol output as its summary.

Run: venv/bin/python3 -m evolution.test_stream_limit
"""
import asyncio
import json
import sys

from agent_runner import STREAM_LINE_LIMIT

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail and not cond else ''}")


async def _drain(limit):
    """Emit init, an oversized event, then result — mirroring a long agy turn."""
    big = json.dumps({"event": "step_update",
                      "step_update": {"text_delta": "x" * 200_000}})
    result = json.dumps({"event": "result",
                         "result": {"response": "the real summary",
                                    "usage": {"input_tokens": 123, "total_tokens": 456}}})
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c",
        "import sys\nfor a in sys.argv[1:]: print(a)",
        json.dumps({"event": "init"}), big, result,
        stdout=asyncio.subprocess.PIPE, limit=limit)

    lines, found, aborted = [], None, None
    while True:
        try:
            raw = await proc.stdout.readline()
        except Exception as e:
            aborted = type(e).__name__
            break
        if not raw:
            break
        s = raw.decode("utf-8", "replace").strip()
        if not s:
            continue
        lines.append(s)
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if obj.get("event") == "result":
            found = obj["result"]
    await proc.wait()
    return lines, found, aborted


def main():
    print("=== Stream limit ===")
    check("STREAM_LINE_LIMIT is well above asyncio's 64 KiB default",
          STREAM_LINE_LIMIT > 64 * 1024, str(STREAM_LINE_LIMIT))

    # The old behaviour, reproduced: 64 KiB limit loses the terminating event.
    lines, found, aborted = asyncio.run(_drain(64 * 1024))
    check("at the old 64 KiB limit the stream aborts", aborted is not None, str(aborted))
    check("at the old limit the result event is lost", found is None, str(found))

    # With the configured limit the whole stream survives.
    lines, found, aborted = asyncio.run(_drain(STREAM_LINE_LIMIT))
    check("at the configured limit the stream completes", aborted is None, str(aborted))
    check("all three events are read", len(lines) == 3, str(len(lines)))
    check("result event recovered", bool(found) and found["response"] == "the real summary", str(found))
    check("usage recovered", bool(found) and found["usage"]["input_tokens"] == 123, str(found))

    print("=== Fallback salvage ===")
    # Even with no result event, the fallback must not echo protocol noise.
    import agent_runner
    src = open(agent_runner.__file__, encoding="utf-8").read()
    check("fallback scans NDJSON line by line", 'obj.get("event") == "result"' in src)
    check("fallback reassembles text_delta fragments", 'deltas.append(frag)' in src)
    check("stream abort is logged, not swallowed", "stdout stream aborted" in src)

    print("\n" + (f"ALL PASS ({len(_results)})" if all(_results)
                  else f"FAILURES: {_results.count(False)}/{len(_results)}"))
    return 0 if all(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
