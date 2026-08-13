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

# Tokyo Night. Truecolor where the terminal advertises it (Warp sets COLORTERM=truecolor),
# with a 256-colour fallback so the output degrades rather than breaking over plain SSH.
#
# Palette from the reference theme:
#   blue   #7aa2f7   cyan   #7dcfff   green  #9ece6a   purple #bb9af7
#   orange #ff9e64   red    #f7768e   yellow #e0af68   fg     #c0caf5
#   comment #565f89  dark3  #545c7e
def _rgb(r: int, g: int, b: int) -> str:
    return "\033[38;2;%d;%d;%dm" % (r, g, b)


_TRUECOLOR = os.environ.get("COLORTERM", "") in ("truecolor", "24bit")

if _TRUECOLOR:
    _C = {
        "id":   _rgb(0x7A, 0xA2, 0xF7),   # blue    - entry ids
        "date": _rgb(0x56, 0x5F, 0x89),   # comment - timestamps
        "q":    _rgb(0xC0, 0xCA, 0xF5) + "\033[1m",   # fg bold - the question
        "a":    _rgb(0xA9, 0xB1, 0xD6),   # fg dim  - the answer
        "ev":   _rgb(0x9E, 0xCE, 0x6A),   # green   - measurements
        "ref":  _rgb(0xE0, 0xAF, 0x68),   # yellow  - commits and files
        "tag":  _rgb(0xBB, 0x9A, 0xF7),   # purple  - tags and projects
        "warn": _rgb(0xFF, 0x9E, 0x64),   # orange  - superseded
        "hit":  _rgb(0x7D, 0xCF, 0xFF),   # cyan    - matched terms
        "dim":  _rgb(0x54, 0x5C, 0x7E),   # dark3
        "rule": _rgb(0x3B, 0x42, 0x61),   # bg_highlight - separators
        "off":  "\033[0m",
    }
else:
    _C = {
        "id": "\033[38;5;111m", "date": "\033[38;5;60m",
        "q": "\033[38;5;189m\033[1m", "a": "\033[38;5;146m",
        "ev": "\033[38;5;149m", "ref": "\033[38;5;179m",
        "tag": "\033[38;5;141m", "warn": "\033[38;5;215m",
        "hit": "\033[38;5;117m", "dim": "\033[38;5;60m",
        "rule": "\033[38;5;237m", "off": "\033[0m",
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
    """Render one entry as a block.

    Each field gets its own colour so the eye can jump straight to the part it wants -
    usually the green evidence lines, which are the verbatim measurements.
    """
    parts = []

    head = "%s  %s" % (c("id", "#%s" % e["id"]), c("date", (e.get("created_at") or "")[:10]))
    if not e.get("confidence", 1):
        head += "  " + c("dim", "auto")
    if e.get("superseded_by"):
        head += "  " + c("warn", "⊘ superseded by #%s" % e["superseded_by"])
    if e.get("project"):
        head += "  " + c("tag", e["project"])
    parts.append(head)

    # Question: bold foreground, the thing you scan for.
    parts.append(wrap(c("q", " ".join(e["question"].split())), indent=2, first_prefix="  "))

    if show_answer:
        ans = " ".join(e["answer"].split())
        if not verbose and len(ans) > 320:
            ans = ans[:320] + "…"
        parts.append(wrap(c("a", ans), indent=2, first_prefix="  "))

    # Evidence: green, with a gutter bar. These are the raw numbers, quoted verbatim, and are
    # the reason to trust an entry rather than merely believe it.
    for line in (e.get("evidence") or [])[: (30 if verbose else 3)]:
        parts.append(wrap(c("ev", " ".join(line.split())),
                          indent=6, first_prefix="   " + c("rule", "│ ")))

    if e.get("refs"):
        parts.append("   " + c("rule", "│ ") + c("ref", "  ".join(e["refs"][:6])))

    if verbose and e.get("tags"):
        parts.append("   " + c("rule", "│ ") + c("tag", e["tags"]))

    return "\n".join(parts)


def table_row(e: dict, qw: int) -> str:
    """One compact line for `recall list`.

    Hand-written entries are shown in full foreground; auto-extracted ones are dimmed, so the
    curated material stands out at a glance without needing a legend.
    """
    q = " ".join(e["question"].split())
    if len(q) > qw:
        q = q[: qw - 1] + "…"

    explicit = bool(e.get("confidence", 1))
    if e.get("superseded_by"):
        marker, qtext = c("warn", "⊘"), c("dim", q)
    elif explicit:
        marker, qtext = c("ev", "●"), c("q", q)
    else:
        marker, qtext = c("dim", "○"), c("dim", q)

    return "%s %s %s %s" % (
        c("id", "%4s" % ("#%s" % e["id"])),
        c("date", (e.get("created_at") or "")[:10]),
        marker,
        qtext,
    )
