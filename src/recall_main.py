#!/usr/bin/env python3
"""
recall_main — shared CLI entry point for recall.
Used by both src/recall (bash wrapper) and src/recall.ps1 (PowerShell wrapper).
"""

import argparse
import os
import sys

# Ensure we can import from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recall_store  # noqa: E402
import recall_fmt as F  # noqa: E402


def _print_entry(e, verbose=False):
    print(F.entry(e, verbose=verbose))
    print()


def cmd_search(args):
    hits = recall_store.search(
        args.query,
        limit=args.limit,
        include_superseded=args.all,
        min_confidence=(1 if args.explicit_only else 0),
    )
    if not hits:
        print(F.c("warn", "no match for %r" % args.query))
        print(F.c("dim", "multi-word queries need 2 matching terms; try fewer, "
                         "more distinctive words"))
        return 1
    print(F.c("dim", "%d result(s) for %r" % (len(hits), args.query)))
    print(F.rule())
    for h in hits:
        _print_entry(h, verbose=args.verbose)
    return 0


def cmd_list(args):
    entries = recall_store.list_entries(
        limit=args.limit,
        include_superseded=args.all,
        explicit_only=args.explicit_only,
        project=args.project or "",
        order=("oldest" if args.oldest else "newest"),
    )
    if not entries:
        print(F.c("dim", "store is empty"))
        return 1

    if args.full:
        print(F.c("dim", "%d entr%s" % (len(entries), "y" if len(entries) == 1 else "ies")))
        print(F.rule())
        for e in entries:
            _print_entry(e, verbose=True)
        return 0

    # Compact table: one line each, so a whole store fits on a screen.
    qw = max(F.width() - 22, 30)
    print("%s   %s %s   %s %s   %s %s" % (
        F.c("dim", "%d entr%s" % (len(entries), "y" if len(entries) == 1 else "ies")),
        F.c("ev", "●"), F.c("dim", "curated"),
        F.c("dim", "○"), F.c("dim", "auto-extracted"),
        F.c("warn", "⊘"), F.c("dim", "superseded")))
    print(F.rule())
    for e in entries:
        print(F.table_row(e, qw))
    print(F.rule())
    print(F.c("dim", "recall show <id>   for one entry in full"))
    return 0


def cmd_prune(args):
    """Re-apply the current extraction rules to stored auto-extracted entries."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import recall_extract as extract

    doomed = [
        e for e in recall_store.list_entries(include_superseded=True)
        if not e.get("confidence") and not extract.is_real_question(e["question"])
    ]
    if not doomed:
        print(F.c("dim", "nothing to prune - every auto-extracted entry still passes"))
        return 0

    print(F.c("dim", "%d auto-extracted entr%s no longer pass the rules:"
                     % (len(doomed), "y" if len(doomed) == 1 else "ies")))
    for e in doomed:
        print("  %s %s" % (F.c("id", "#%s" % e["id"]),
                           " ".join(e["question"].split())[:66]))
    if args.dry_run:
        print(F.c("dim", "\ndry run - nothing deleted. Re-run without --dry-run to remove."))
        return 0

    conn = recall_store.connect()
    try:
        for e in doomed:
            conn.execute("DELETE FROM entries WHERE id = ? AND confidence = 0", (e["id"],))
        conn.commit()
    finally:
        conn.close()
    print(F.c("dim", "\nremoved %d entr%s" % (len(doomed), "y" if len(doomed) == 1 else "ies")))
    return 0


def cmd_projects(args):
    rows = recall_store.projects()
    if not rows:
        print(F.c("dim", "store is empty"))
        return 1
    for name, n in rows:
        print("%s  %s" % (F.c("id", "%4d" % n), F.c("tag", name)))
    return 0


def cmd_add(args):
    dup = recall_store.find_duplicate(args.question)
    if dup and not args.force:
        print(F.c("warn", "a similar entry already exists:") + "\n")
        _print_entry(dup)
        print(F.c("dim", "--force to add anyway, or after adding the correction:"))
        print(F.c("dim", "  recall supersede %s --by <new-id>" % dup["id"]))
        return 2
    new_id = recall_store.add(
        question=args.question,
        answer=args.answer,
        evidence=args.evidence,
        refs=args.ref,
        tags=args.tags or "",
        project=args.project or "",
        session_id=args.session or "",
        source="explicit",
        confidence=1,
    )
    print("stored as " + F.c("id", "#%d" % new_id))
    if args.supersedes:
        recall_store.supersede(args.supersedes, new_id)
        print("marked #%s as superseded by #%d" % (args.supersedes, new_id))
    return 0


def cmd_show(args):
    e = recall_store.get(args.id)
    if not e:
        print(F.c("warn", "no entry #%s" % args.id))
        return 1
    _print_entry(e, verbose=True)
    return 0


def cmd_supersede(args):
    if not recall_store.get(args.old):
        print("no entry #%s" % args.old)
        return 1
    if not recall_store.get(args.by):
        print("no entry #%s" % args.by)
        return 1
    recall_store.supersede(args.old, args.by)
    print(F.c("warn", "#%s" % args.old) + " superseded by " + F.c("id", "#%s" % args.by))
    return 0


def cmd_stats(args):
    s = recall_store.stats()
    print(F.c("dim", recall_store.DB_PATH))
    print(F.rule())
    print("  %s  %s" % (F.c("id", "%4d" % s["total"]), "entries"))
    print("  %s  %s" % (F.c("ev", "%4d" % s["explicit"]), F.c("q", "curated")))
    print("  %s  %s" % (F.c("dim", "%4d" % s["extracted"]), F.c("dim", "auto-extracted")))
    print("  %s  %s" % (F.c("warn", "%4d" % s["superseded"]), F.c("warn", "superseded")))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="recall", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="find an answer")
    s.add_argument("query")
    s.add_argument("-n", "--limit", type=int, default=5)
    s.add_argument("-v", "--verbose", action="store_true", help="full answer and evidence")
    s.add_argument("--all", action="store_true", help="include superseded entries")
    s.add_argument("--explicit-only", action="store_true", help="skip auto-extracted entries")
    s.set_defaults(func=cmd_search)

    a = sub.add_parser("add", help="record an answer")
    a.add_argument("-q", "--question", required=True)
    a.add_argument("-a", "--answer", required=True)
    a.add_argument("-e", "--evidence", action="append", help="repeatable, verbatim measurement")
    a.add_argument("--ref", action="append", help="repeatable: commit sha, file:line")
    a.add_argument("--tags", default="")
    a.add_argument("--project", default="")
    a.add_argument("--session", default="")
    a.add_argument("--supersedes", type=int, help="id this entry corrects")
    a.add_argument("--force", action="store_true", help="add even if a duplicate exists")
    a.set_defaults(func=cmd_add)

    ls = sub.add_parser("list", help="browse every entry")
    ls.add_argument("-n", "--limit", type=int, default=0, help="cap the number shown")
    ls.add_argument("-f", "--full", action="store_true", help="full entries, not one line each")
    ls.add_argument("--all", action="store_true", help="include superseded entries")
    ls.add_argument("--explicit-only", action="store_true", help="skip auto-extracted")
    ls.add_argument("--project", default="", help="filter to one project label")
    ls.add_argument("--oldest", action="store_true", help="oldest first")
    ls.set_defaults(func=cmd_list)

    pn = sub.add_parser("prune", help="drop auto-entries that fail the current rules")
    pn.add_argument("--dry-run", action="store_true", help="list them without deleting")
    pn.set_defaults(func=cmd_prune)

    pr = sub.add_parser("projects", help="entry count per project")
    pr.set_defaults(func=cmd_projects)

    sh = sub.add_parser("show", help="print one entry in full")
    sh.add_argument("id", type=int)
    sh.set_defaults(func=cmd_show)

    sp = sub.add_parser("supersede", help="mark an entry as corrected")
    sp.add_argument("old", type=int)
    sp.add_argument("--by", type=int, required=True)
    sp.set_defaults(func=cmd_supersede)

    st = sub.add_parser("stats", help="store summary")
    st.set_defaults(func=cmd_stats)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())