"""
recall_fmt — terminal rendering for the recall CLI.

Colour is opt-out, not opt-in: it is enabled when stdout is a TTY and disabled the moment
output is piped or redirected, so `recall list | grep` and `recall search > file` stay clean.
NO_COLOR is honoured (https://no-color.org). RECALL_COLOR=always forces it on for pagers that
handle ANSI, e.g. `recall list | less -R`.

Widths adapt to the terminal, so this behaves in a narrow split as well as a full window.
"""

import os
import shutil
import sys

# 256-colour codes, chosen to stay readable on both dark and light backgrounds.
_C = {
    "id": "\033[38;5;110m",       # soft blue
    "date": "\033[38;5;245m",     # grey
    "q": "\033[1m",               # bold
    "a": "\033[0m",               # default
    "ev": "\033[38;5;108m",       # green - measurements
    "ref": "\033[38;5;180m",      # tan - commits and files
    "tag": "\033[38;5;139m",      # muted purple
    "warn": "\033[38;5;209m",     # orange - superseded
    "dim": "\033[2m",
    "rule": "\033[38;5;238m",     # near-black separator
    "off": "\033[0m",
}


def _use_colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("RECALL_COLOR") == "always":
        return True
    if os.environ.get("RECALL_COLOR") == "never":
        return False
    return sys.stdout.isatty()


COLOUR = _use_colour()


def c(key: str, text) -> str:
    """Wrap *text* in colour *key*, or return it unchanged when colour is off."""
    if not COLOUR:
        return str(text)
    return "%s%s%s" % (_C.get(key, ""), text, _C["off"])


def width(default: int = 100) -> int:
    """Usable line width, clamped so very wide windows stay readable."""
    try:
        return min(shutil.get_terminal_size().columns, 120)
    except Exception:  # noqa: BLE001
        return default


def wrap(text: str, indent: int = 5, first_prefix: str = "") -> str:
    """Wrap *text* to the terminal, indenting continuation lines.

    Written by hand rather than with textwrap because the prefix is coloured: textwrap counts
    escape sequences as visible characters and wraps far too early.
    """
    import textwrap

    avail = max(width() - indent - 2, 30)
    body = " ".join(str(text).split())
    lines = textwrap.wrap(body, avail) or [""]
    pad = " " * indent
    out = [first_prefix + lines[0]]
    out += [pad + ln for ln in lines[1:]]
    return "\n".join(out)


def rule(char: str = "─") -> str:
    return c("rule", char * width())


def entry(e: dict, verbose: bool = False, show_answer: bool = True) -> str:
    """Render one entry as a block."""
    parts = []

    head = "%s  %s" % (c("id", "#%s" % e["id"]), c("date", (e.get("created_at") or "")[:10]))
    if not e.get("confidence", 1):
        head += "  " + c("dim", "auto")
    if e.get("superseded_by"):
        head += "  " + c("warn", "superseded by #%s" % e["superseded_by"])
    if e.get("project"):
        head += "  " + c("tag", e["project"])
    parts.append(head)

    parts.append(wrap(e["question"], indent=2, first_prefix="  " + c("q", "")) if not COLOUR
                 else wrap(c("q", " ".join(e["question"].split())), indent=2, first_prefix="  "))

    if show_answer:
        ans = e["answer"] if verbose else e["answer"][:280] + (
            "…" if len(e["answer"]) > 280 else "")
        parts.append(wrap(ans, indent=2, first_prefix="  "))

    for line in (e.get("evidence") or [])[: (30 if verbose else 3)]:
        parts.append(wrap(c("ev", line), indent=6, first_prefix="    " + c("ev", "│ ")))

    if e.get("refs"):
        parts.append("    " + c("ref", " ".join(e["refs"][:6])))

    if verbose and e.get("tags"):
        parts.append("    " + c("tag", e["tags"]))

    return "\n".join(parts)


def table_row(e: dict, qw: int) -> str:
    """One compact line for `recall list`."""
    q = " ".join(e["question"].split())
    if len(q) > qw:
        q = q[: qw - 1] + "…"
    flag = " " if e.get("confidence", 1) else c("dim", "a")
    if e.get("superseded_by"):
        flag = c("warn", "s")
    return "%s %s %s  %s" % (
        c("id", "%4s" % ("#%s" % e["id"])),
        c("date", (e.get("created_at") or "")[:10]),
        flag,
        q.ljust(qw) if COLOUR else q,
    )
