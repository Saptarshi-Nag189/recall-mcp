"""
recall_extract — pull question/answer pairs out of a Claude Code transcript.

Deterministic. No model call, no network. The same rules run over the 56 existing transcripts
for the initial backfill and over each new session afterwards, so there is no separate "one
time" pass to maintain.

The filter chain, and why each step is there
--------------------------------------------
Measured on one real session: 2457 lines of type "user" reduce to 149 genuine questions, and
most of those ("ok continue", "yes") are not worth keeping. Four rules do the work:

1. A real user turn has *string* content. Tool results arrive as a list of blocks, so
   isinstance(content, str) removes the bulk of the noise in one test.

2. Harness traffic announces itself with a known prefix - <local-command, <system-reminder,
   [Request interrupted, Caveat:. Prefix matching is exact and cheap.

3. The answer must carry HARD EVIDENCE: a number with a unit, a commit sha, a file:line, or an
   explicit before/after arrow. This is the rule that matters. "ok continue" -> "Sure, doing
   that now" has nothing to retrieve later; "how much did caching gain?" -> "0.703s -> 0.0014s"
   does. Keeping only evidence-bearing pairs is what stops the store filling with chatter.

4. Length bounds on both sides: a 4-character question or a 20-line answer is not a lookup.

Extracted entries are stored with confidence=0 so they rank below anything written explicitly,
and are labelled in search output. This half of the capture is a baseline; it will miss things
and keep some noise, and it is not a substitute for saying "save this".
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recall_store  # noqa: E402

# ── rule 2: harness traffic, not the user talking ────────────────────────────
NOISE_PREFIXES = (
    "<local-command",
    "<command-",
    "<system-reminder",
    "<user-prompt-submit-hook",
    "[Request interrupted",
    "Caveat:",
    "This session is being continued",
    "<bash-",
)

# Short acknowledgements. No answer to them is worth a lookup.
FILLER = {
    "ok", "okay", "yes", "no", "y", "n", "sure", "thanks", "thank you", "continue",
    "ok continue", "go on", "proceed", "do it", "do that", "next", "yep", "yeah",
    "carry on", "keep going", "fine", "good", "great", "perfect", "nice", "done",
    "stop", "wait", "hold on", "hmm", "k",
}

# ── rule 3: what counts as hard evidence in an answer ────────────────────────
EVIDENCE_PATTERNS = [
    # a number with a unit: 0.703s, 2217.6 ms, 32 MB, 41 requests
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s\b|sec|seconds|MB|KB|GB|x\b|%)", re.I),
    # a before/after transition: 5 -> 32, 0.70s → 0.0014s
    re.compile(r"\d[\d.]*\s*(?:->|→|to)\s*\d[\d.]*"),
    # a git sha as written in this project's messages
    re.compile(r"\b[0-9a-f]{7,40}\b"),
    # a source location
    re.compile(r"\b[\w/]+\.(?:py|js|sh|rego|ino|json|yml|yaml|md):\d+"),
    # an explicit count out of a total: 32 of 41, 14/14
    re.compile(r"\b\d+\s*(?:of|/)\s*\d+\b"),
]

# Lines inside an answer that carry a measurement, kept verbatim as evidence.
#
# Deliberately a SEARCH, not a match anchored at the start of the line. The first version
# anchored with ^ and required the number early in the line, which only caught measurements
# formatted as list items. Most real answers put the figure mid-sentence or inside a markdown
# table, so 17 answers with evidence yielded just 1 quotable line. Searching anywhere in the
# line took that to a usable rate.
MEASUREMENT_LINE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:ms\b|s\b|sec\b|MB\b|KB\b|GB\b|x\b|%|/\d+)"
    r"|\d[\d.]*\s*(?:->|→)\s*\d[\d.]*"
    r"|\b[0-9a-f]{7,40}\b"
    r"|\.(?:py|js|sh|rego|ino):\d+",
    re.I,
)

SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
FILE_REF_RE = re.compile(r"\b[\w/\-]+\.(?:py|js|sh|rego|ino|json|yml|yaml|md)(?::\d+)?")

MIN_Q, MAX_Q = 8, 400
MIN_A, MAX_A = 40, 4000


def _text_of(message) -> str:
    """Flatten an assistant message's content blocks to plain text."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


# Questions that are only meaningful inside the conversation that produced them. "anything
# else?" or "you have the context?" have perfectly good answers at the time and no value at all
# six weeks later, because the words carry no subject to search on.
CONTEXTLESS = re.compile(
    r"^(?:"
    r"anything else|what else|and\??|you have the context|do you have the context|"
    r"is it done|are you done|whats? next|what now|continue.*|go ahead.*|"
    r"explain \d+|that one|this one|which one|why\??|how\??|ok.*|"
    r"do (?:it|that|this|all)|leave (?:it|that)|same|again|retry|redo"
    r")[\s?.!]*$",
    re.I,
)

# A durable question names something: a file, a metric, a subsystem, a number.
SUBJECT_HINT = re.compile(
    r"[\w/]+\.(?:py|js|sh|rego|ino|json|yml|md)"          # a filename
    r"|\b\d+"                                              # a figure
    r"|\b(?:cache|caching|pool|score|weight|rule|policy|opa|rego|bundle|gateway|"
    r"dashboard|endpoint|commit|latency|slow|fast|fail|error|health|probe|"
    r"trust|authz|auth|sensor|firmware|wifi|device|db|database|query|ci)\b",
    re.I,
)


def is_real_question(text: str) -> bool:
    """Rules 1, 2 and 4 applied to a user turn."""
    if not isinstance(text, str):
        return False
    s = text.strip()
    if not (MIN_Q <= len(s) <= MAX_Q):
        return False
    if s.startswith(NOISE_PREFIXES):
        return False
    if s.lower().strip("?!. ") in FILLER:
        return False
    if CONTEXTLESS.match(s):
        return False
    # Must name a subject, or it cannot be found again by searching for one.
    return bool(SUBJECT_HINT.search(s))


def has_evidence(text: str) -> bool:
    """Rule 3: does this answer contain something worth retrieving later?"""
    return any(p.search(text) for p in EVIDENCE_PATTERNS)


def evidence_lines(text: str, limit: int = 6):
    """Pull the measurement-bearing lines out, verbatim.

    Verbatim matters: the whole failure this store addresses was a remembered number being
    wrong. Storing the raw line lets a reader check the claim rather than trust it.
    """
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "```")):
            continue
        if MEASUREMENT_LINE.search(line) and 8 < len(line) < 220:
            # Markdown table rows carry the numbers in this project's answers; normalise the
            # pipes to a readable form rather than dropping the row.
            cleaned = re.sub(r"\*\*|`", "", line)
            cleaned = re.sub(r"\s*\|\s*", " | ", cleaned).strip(" |-*")
            cleaned = re.sub(r"\s+", " ", cleaned)
            if cleaned and not set(cleaned) <= set("|-: ") and cleaned not in out:
                out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def summarise(text: str, max_chars: int = 400) -> str:
    """First substantive prose of the answer, as the stored conclusion."""
    for para in text.split("\n\n"):
        p = " ".join(para.split())
        if not p or p.startswith(("```", "|", "#")):
            continue
        p = re.sub(r"\*\*|`", "", p)
        if len(p) >= 40:
            return p[:max_chars]
    flat = re.sub(r"\s+", " ", re.sub(r"\*\*|`", "", text)).strip()
    return flat[:max_chars]


def derive_tags(question: str, answer: str) -> str:
    """Keyword tags to widen FTS recall beyond the words literally used."""
    vocab = {
        "cache": "cache caching performance",
        "pool": "pool connection database",
        "slow": "performance latency",
        "fast": "performance",
        "score": "trust_score policy scoring",
        "trust": "trust_score policy",
        "rego": "policy opa rego",
        "opa": "policy opa",
        "flash": "firmware esp32 enddevice",
        "wifi": "wifi firmware protocol",
        "gateway": "gateway CDAC_chn_gw",
        "dashboard": "dashboard ui frontend",
        "commit": "git",
        "test": "testing verification",
        "ci": "ci pipeline gitlab",
        "bundle": "bundle opa server",
        "health": "health probe startup",
    }
    blob = (question + " " + answer[:500]).lower()
    tags = set()
    for key, extra in vocab.items():
        if key in blob:
            tags.update(extra.split())
    return " ".join(sorted(tags))


def extract_file(path: str, project: str = "", limit_pairs: int = 0):
    """Return a list of candidate entries from one transcript."""
    pairs, pending = [], None
    session_id = os.path.basename(path).replace(".jsonl", "")

    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue

            rtype = rec.get("type")
            if rtype == "user":
                text = rec.get("message", {}).get("content")
                pending = text.strip() if is_real_question(text) else None

            elif rtype == "assistant" and pending:
                answer = _text_of(rec.get("message", {}))
                if not (MIN_A <= len(answer) <= MAX_A) or not has_evidence(answer):
                    continue
                ev = evidence_lines(answer)
                if not ev:
                    continue  # evidence matched somewhere, but nothing quotable
                refs = list(dict.fromkeys(
                    SHA_RE.findall(answer)[:3] + FILE_REF_RE.findall(answer)[:3]
                ))
                pairs.append(dict(
                    question=pending,
                    answer=summarise(answer),
                    evidence=ev,
                    refs=refs,
                    tags=derive_tags(pending, answer),
                    project=project or "",
                    session_id=session_id,
                    source="extracted",
                    confidence=0,
                ))
                pending = None
                if limit_pairs and len(pairs) >= limit_pairs:
                    break
    return pairs


def ingest(path: str, project: str = "", dry_run: bool = False, db_path: str = None):
    """Extract from one transcript and store, skipping near-duplicates."""
    pairs = extract_file(path, project=project)
    if dry_run:
        return pairs, 0
    conn = recall_store.connect(db_path)
    added = 0
    try:
        for p in pairs:
            if recall_store.find_duplicate(p["question"], conn=conn):
                continue
            recall_store.add(conn=conn, **p)
            added += 1
    finally:
        conn.close()
    return pairs, added


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Extract Q&A pairs from Claude transcripts.")
    ap.add_argument("paths", nargs="+", help="transcript .jsonl files or directories")
    ap.add_argument("--project", default="", help="project label to store with each entry")
    ap.add_argument("--dry-run", action="store_true", help="show what would be stored")
    ap.add_argument("--limit", type=int, default=0, help="max pairs to print in dry-run")
    args = ap.parse_args()

    files = []
    for p in args.paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in names if n.endswith(".jsonl")]
        elif p.endswith(".jsonl"):
            files.append(p)

    total_found = total_added = 0
    for f in files:
        pairs, added = ingest(f, project=args.project, dry_run=args.dry_run)
        total_found += len(pairs)
        total_added += added
        if pairs:
            print(f"{os.path.basename(f)}: {len(pairs)} candidates, {added} stored")
            if args.dry_run:
                for p in pairs[: (args.limit or 3)]:
                    print(f"    Q: {p['question'][:80]}")
                    print(f"    A: {p['answer'][:80]}")
                    print(f"    E: {p['evidence'][:2]}")
    print(f"\n{len(files)} transcript(s): {total_found} candidates, {total_added} stored")


if __name__ == "__main__":
    main()
