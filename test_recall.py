#!/usr/bin/env python3
"""
Regression tests for recall. Stdlib only: `python3 test_recall.py`.

Each case here exists because something actually went wrong. Search quality is a balance
between recall (finding what exists) and precision (not inventing relevance), and every past
tweak to one silently damaged the other. This pins both ends so the next tweak is measured.
"""

import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

TMP = tempfile.mkdtemp(prefix="recall_test_")
os.environ["RECALL_DB"] = os.path.join(TMP, "test.db")

import recall_store as R  # noqa: E402
import recall_extract as E  # noqa: E402

R.DB_PATH = os.environ["RECALL_DB"]

PASS = FAIL = 0


def check(name, got, want=None, cond=None):
    global PASS, FAIL
    ok = cond(got) if cond else (got == want)
    if ok:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s\n         got %r, wanted %r" % (name, got, want if want is not None
                                                         else "<condition>"))


def seed():
    """A miniature store shaped like the real one."""
    R.add(question="how much did the Auth Dashboard caching optimisation gain? performance",
          answer="Cache on /api/policy_status: 0.703s -> 0.0014s, about 500x.",
          evidence=["call 1: 0.703146s (cold) -> call 2: 0.001370s"],
          refs=["6b6b61057"], tags="cache caching performance latency dashboard speedup")
    R.add(question="why was DB_POOL_SIZE changed? pool exhausted 500 errors",
          answer="5 -> 32 -> 16. Raising it alone made things worse; pool creation had no lock.",
          evidence=["31 of 41 requests returned HTTP 500 'pool exhausted'"],
          tags="pool connection database scaling concurrency")
    R.add(question="how is recall registered with Antigravity (agy)? which config file",
          answer="Server definition in ~/.gemini/config/mcp_config.json; permissions separately.",
          evidence=["verified unprompted 2026-08-13"],
          tags="agy antigravity mcp config registration setup")
    R.add(question="why does recall use SQLite FTS5 instead of a vector database?",
          answer="~1k entries; FTS5 is stdlib, no embedding model, no tokens per query.",
          evidence=["measured 0.74 ms per search"],
          tags="vector database fts5 sqlite embeddings design")
    R.add(question="an auto-extracted entry", answer="body mentions caching in passing only.",
          source="extracted", confidence=0, tags="")


def test_recall_quality():
    print("\nRECALL - natural phrasing finds the right entry")
    cases = [
        ("how fast is the dashboard now", "caching"),
        ("how many connections should the pool have", "POOL_SIZE"),
        ("where do I configure antigravity", "Antigravity"),
        ("why not use embeddings", "FTS5"),
        ("caching gain", "caching"),
    ]
    for query, expect_in_question in cases:
        hits = R.search(query, limit=3)
        got = any(expect_in_question.lower() in h["question"].lower() for h in hits)
        check("%-42s -> finds %r" % (query, expect_in_question), got, True)


def test_precision():
    print("\nPRECISION - nonsense returns nothing")
    for query in ["xyzzy nothing here", "what is nothing here", "", "   ",
                  "completely unrelated gibberish words"]:
        check("%-42s -> 0 hits" % (query or "<empty>"), len(R.search(query, limit=5)), 0)


def test_ranking():
    print("\nRANKING - the entry ABOUT a topic beats one that mentions it")
    hits = R.search("caching gain", limit=3)
    check("caching query ranks the measurement first",
          "0.703s" in hits[0]["answer"] if hits else False, True)


def test_robustness():
    print("\nROBUSTNESS - malformed input never raises")
    for query in ['"', "'", "((", "*", "NOT", "OR", "AND", "a", "x" * 500,
                  "'; DROP TABLE entries;--", "../../etc/passwd", "🔥", "null\x00byte"]:
        try:
            R.search(query, limit=3)
            check("query %r survives" % query[:18], True, True)
        except Exception as exc:  # noqa: BLE001
            check("query %r survives" % query[:18], "%s: %s" % (type(exc).__name__, exc), True)
    check("store intact after injection attempt", R.stats()["total"], 5)


def test_supersede():
    print("\nSUPERSEDE - corrected answers stop being served")
    old = R.add(question="what caused the CI failure? OOM theory", answer="Out of memory.")
    new = R.add(question="what caused the CI failure? real cause",
                answer="Probe timeout on a cold DB connect.", tags="ci failure probe")
    R.supersede(old, new)
    ids = [h["id"] for h in R.search("CI failure cause", limit=5)]
    check("superseded entry hidden by default", old in ids, False)
    ids_all = [h["id"] for h in R.search("CI failure cause", limit=5, include_superseded=True)]
    check("superseded entry visible with --all", old in ids_all, True)


def test_extractor():
    print("\nEXTRACTOR - deterministic rules keep signal, drop chatter")
    keep = [
        "how much did the caching change gain?",
        "why does the pool need a lock?",
        "what should I do if I change the ip from 53 to 43?",
    ]
    drop = [
        "ok", "yes", "continue", "anything else?", "stuck in 3",
        "43 is indeed on a diffrent device",
        "i don't need retry fix (1ac4ce854)",
        "the price is 3,000,000 INR. Is it justifiable?",
        "Downloading nvidia_nccl_cu12-2.30.7-py3-none-manylinux_2_18_x86_64.whl",
        "sudo /home/sapta/.acme.sh/acme.sh --install-cert -d zt-server",
        "<system-reminder>something</system-reminder>",
    ]
    for q in keep:
        check("keeps  %r" % q[:40], E.is_real_question(q), True)
    for q in drop:
        check("drops  %r" % q[:40], E.is_real_question(q), False)


def test_evidence_rules():
    print("\nEVIDENCE - an answer must carry something checkable")
    check("number with a unit", E.has_evidence("it took 0.703s to run"), True)
    check("before/after arrow", E.has_evidence("went from 5 -> 32"), True)
    check("commit sha", E.has_evidence("fixed in 6b6b61057 yesterday"), True)
    check("file:line", E.has_evidence("see zt_shared/policy_engine.py:167"), True)
    check("prose only", E.has_evidence("I made the change and it seems fine now"), False)


def test_latency():
    print("\nLATENCY")
    R.search("warm up")
    start = time.perf_counter()
    for _ in range(100):
        R.search("caching gain performance")
    per = (time.perf_counter() - start) / 100 * 1000
    check("search under 10 ms (was %.2f ms)" % per, per < 10, True)


def main():
    seed()
    try:
        test_recall_quality()
        test_precision()
        test_ranking()
        test_robustness()
        test_supersede()
        test_extractor()
        test_evidence_rules()
        test_latency()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
