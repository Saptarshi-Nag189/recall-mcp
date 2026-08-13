"""
recall_store — the one implementation behind the CLI, the MCP server and the extractor.

Stores question/answer pairs together with the raw evidence that backs them, so a question
already answered can be looked up instead of re-derived.

Why this exists
---------------
On 2026-08-13 the question "how much did yesterday's caching gain?" was answered by re-running
the benchmark rather than reading the recorded numbers. The re-run was badly constructed - a
cold DB connect on one side, a warm pool on the other - and produced "0.5x", then a corrected
"146x". Neither matched the figure actually recorded the day before (1.27s -> 0.01s). The data
was on disk the whole time; nothing made it findable, so re-deriving looked cheaper than
retrieving, and re-deriving got it wrong.

Design notes
------------
* stdlib only. It has to run under the system python3 so other CLIs can use it without a venv.
* SQLite + FTS5, not a vector store. The corpus is ~1k entries; FTS5 is in the stdlib, costs no
  tokens per query and needs no embedding model on every client that wants to read it.
* One module. The CLI, the MCP server and the extractor all call in here rather than
  reimplementing search - the same discipline that the duplicated gateway trust-score table
  failed, where a second copy silently mis-scored 32 of 41 devices.
"""

import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get(
    "RECALL_DB", os.path.join(os.path.expanduser("~"), ".shared_memory", "recall.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id            INTEGER PRIMARY KEY,
  question      TEXT NOT NULL,
  answer        TEXT NOT NULL,
  evidence      TEXT,
  refs          TEXT,
  tags          TEXT,
  project       TEXT,
  session_id    TEXT,
  created_at    TEXT NOT NULL,
  source        TEXT NOT NULL DEFAULT 'explicit',
  confidence    INTEGER NOT NULL DEFAULT 1,
  superseded_by INTEGER
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
  question, answer, evidence, tags,
  content='entries', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
  INSERT INTO entries_fts(rowid, question, answer, evidence, tags)
  VALUES (new.id, new.question, new.answer, new.evidence, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, question, answer, evidence, tags)
  VALUES ('delete', old.id, old.question, old.answer, old.evidence, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, question, answer, evidence, tags)
  VALUES ('delete', old.id, old.question, old.answer, old.evidence, old.tags);
  INSERT INTO entries_fts(rowid, question, answer, evidence, tags)
  VALUES (new.id, new.question, new.answer, new.evidence, new.tags);
END;

CREATE INDEX IF NOT EXISTS idx_entries_session ON entries(session_id);
CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at);
"""


def connect(db_path: str = None) -> sqlite3.Connection:
    """Open (creating if needed) the recall database."""
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _dumps(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    return json.dumps(list(value), ensure_ascii=False)


def _loads(value) -> List[str]:
    if not value:
        return []
    try:
        out = json.loads(value)
        return out if isinstance(out, list) else [str(out)]
    except (ValueError, TypeError):
        return [str(value)]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["evidence"] = _loads(d.get("evidence"))
    d["refs"] = _loads(d.get("refs"))
    return d


# FTS5 treats these as operators. A question pasted verbatim (the common case) would otherwise
# raise "fts5: syntax error", so each bare word is quoted and the terms are AND-ed.
_FTS_SAFE = re.compile(r"[A-Za-z0-9_.:/-]+")


# Words that carry no signal in a question and would otherwise drag OR-ranking around.
_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "do", "does", "did", "we", "i", "you",
    "it", "and", "or", "of", "to", "in", "on", "for", "how", "what", "why", "when",
    "much", "many", "that", "this", "our", "my", "be", "by", "with", "from", "at",
    # Common enough in ordinary prose that they satisfy the two-term floor without carrying
    # meaning: "xyzzy nothing here" matched entries purely on "nothing" and "here".
    "nothing", "here", "there", "some", "any", "all", "not", "no", "yes", "can", "will",
    "would", "should", "could", "have", "has", "had", "get", "got", "make", "made",
    "use", "used", "using", "about", "also", "just", "only", "now", "then", "than",
    "but", "if", "so", "as", "up", "out", "over", "into", "more", "most", "other",
}


def _to_match_query(query: str) -> str:
    """Build an FTS5 MATCH expression, OR-ing the terms.

    OR, not AND. "caching gain" found nothing under AND because the stored entry says
    "caching" but never the word "gain" - and a lookup that only works when you guess the
    original wording is no better than not having the store. bm25 still ranks entries matching
    more terms higher, so the best hit surfaces first; OR only widens what is reachable.
    """
    terms = [t for t in _FTS_SAFE.findall(query or "") if len(t) > 1]
    terms = [t for t in terms if t.lower() not in _STOP]
    # A query made entirely of stop words ("what is nothing here") has nothing to search on.
    # Returning "" makes search() give back nothing, rather than falling through to the raw
    # words and matching almost every entry.
    if not terms:
        return ""
    return " OR ".join('"%s"' % t.replace('"', "") for t in terms)


def _count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]


def _rare_terms(conn: sqlite3.Connection, terms: List[str], ceiling: int) -> List[str]:
    """Terms appearing in at most *ceiling* entries.

    A term found in one or two entries out of two dozen is doing real work; one found in half
    the store is not. Cheap to compute - one COUNT per term against the FTS index.
    """
    rare = []
    for t in terms:
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM entries_fts WHERE entries_fts MATCH ?",
                ('"%s"' % t.replace('"', ""),),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            continue
        if 0 < n <= ceiling:
            rare.append(t.lower())
    return rare


def _question_has(entry: Dict[str, Any], terms: List[str]) -> bool:
    """True when the entry's question or tags carry one of *terms*.

    Question and tags describe what an entry is ABOUT. The answer and evidence often mention a
    word in passing, which is not the same thing and is how irrelevant entries surfaced.
    """
    if not terms:
        return False
    blob = (entry.get("question", "") + " " + (entry.get("tags", "") or "")).lower()
    return any(t in blob for t in terms)


def add(
    question: str,
    answer: str,
    evidence=None,
    refs=None,
    tags: str = "",
    project: str = "",
    session_id: str = "",
    source: str = "explicit",
    confidence: int = 1,
    db_path: str = None,
    conn: sqlite3.Connection = None,
) -> int:
    """Insert one entry, returning its id."""
    own = conn is None
    conn = conn or connect(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO entries
               (question, answer, evidence, refs, tags, project, session_id,
                created_at, source, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                question.strip(),
                answer.strip(),
                _dumps(evidence),
                _dumps(refs),
                (tags or "").strip(),
                project or "",
                session_id or "",
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                source,
                confidence,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if own:
            conn.close()


def search(
    query: str,
    limit: int = 5,
    include_superseded: bool = False,
    min_confidence: int = 0,
    db_path: str = None,
    conn: sqlite3.Connection = None,
) -> List[Dict[str, Any]]:
    """Full-text search, best match first.

    Superseded entries are hidden by default: a corrected answer must not keep being served.
    """
    own = conn is None
    conn = conn or connect(db_path)
    try:
        match = _to_match_query(query)
        if not match:
            return []
        sql = """
            SELECT e.*, bm25(entries_fts) AS score
            FROM entries_fts
            JOIN entries e ON e.id = entries_fts.rowid
            WHERE entries_fts MATCH ?
              AND e.confidence >= ?
        """
        if not include_superseded:
            sql += " AND e.superseded_by IS NULL "
        # Over-fetch, then re-rank by how many query terms the entry actually contains.
        sql += " ORDER BY score, e.created_at DESC LIMIT ?"
        try:
            rows = conn.execute(sql, (match, min_confidence, limit * 6)).fetchall()
        except sqlite3.OperationalError:
            return []

        # bm25 alone ranks on term rarity, so a single rare word can carry an otherwise
        # irrelevant entry to the top: searching "recall search" returned a pasted UI dump
        # because it happened to contain the word "Search". Require a query with several terms
        # to match more than one of them, and prefer entries covering more of the query.
        terms = [t.lower() for t in _FTS_SAFE.findall(query) if len(t) > 1
                 and t.lower() not in _STOP] or \
                [t.lower() for t in _FTS_SAFE.findall(query) if len(t) > 1]
        scored = []
        for r in rows:
            d = _row_to_dict(r)
            q_blob = (d.get("question", "") + " " + (d.get("tags", "") or "")).lower()
            body_blob = " ".join([
                d.get("answer", ""), " ".join(d.get("evidence", [])),
            ]).lower()
            hits = sum(1 for t in terms if t in q_blob or t in body_blob)
            # A term matching in the QUESTION means the entry is about that topic; a term
            # matching only in the body may be an incidental mention. Without this, an entry
            # explaining search behaviour outranked the actual caching measurement for the
            # query "caching gain", purely because it quoted that phrase as an example.
            q_hits = sum(1 for t in terms if t in q_blob)
            d["_hits"] = hits
            d["_qhits"] = q_hits
            scored.append(d)

        if len(terms) > 1:
            multi = [d for d in scored if d["_hits"] >= 2]
            if multi:
                scored = multi
            else:
                # Nothing matched two terms. Rather than return nothing, accept a single-term
                # match when that term is DISTINCTIVE - it appears in few entries, so it is
                # carrying real meaning rather than being a common word.
                #
                # Straight rejection was too strict: "where do I configure antigravity" found
                # nothing even though 'antigravity' appears in exactly one entry, which is
                # obviously the right answer. But accepting any single-term match is what let
                # "recall search" match a pasted UI dump on the word "Search". Rarity is the
                # discriminator between the two.
                # The rare term must appear in the QUESTION or TAGS, not merely somewhere in
                # the body. "recall search" matched four entries when body text counted,
                # because both words are rare in a small store yet appear incidentally in
                # answers about search behaviour. Requiring the question to carry the term is
                # what separates "this entry is about X" from "this entry mentions X".
                rare = _rare_terms(conn, terms, ceiling=max(2, _count(conn) // 8))
                scored = [d for d in scored if _question_has(d, rare)]
        # Rank: most terms matched, then most matched in the question, then bm25.
        scored.sort(key=lambda d: (-d["_hits"], -d["_qhits"], d.get("score", 0)))
        for d in scored:
            d.pop("_hits", None)
            d.pop("_qhits", None)
        return scored[:limit]
    finally:
        if own:
            conn.close()


def list_entries(
    limit: int = 0,
    include_superseded: bool = False,
    explicit_only: bool = False,
    project: str = "",
    order: str = "newest",
    db_path: str = None,
    conn: sqlite3.Connection = None,
) -> List[Dict[str, Any]]:
    """Every entry, newest first by default. ``limit=0`` means no limit.

    Browsing, as opposed to searching: no MATCH, so nothing is filtered by keyword.
    """
    own = conn is None
    conn = conn or connect(db_path)
    try:
        sql = "SELECT * FROM entries WHERE 1=1"
        args: List[Any] = []
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        if explicit_only:
            sql += " AND confidence > 0"
        if project:
            sql += " AND project = ?"
            args.append(project)
        sql += " ORDER BY id ASC" if order == "oldest" else " ORDER BY id DESC"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return [_row_to_dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        if own:
            conn.close()


def projects(db_path: str = None, conn: sqlite3.Connection = None) -> List[tuple]:
    """(project, count) pairs, busiest first."""
    own = conn is None
    conn = conn or connect(db_path)
    try:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(project,''),'(none)') p, COUNT(*) n "
            "FROM entries GROUP BY p ORDER BY n DESC"
        ).fetchall()
        return [(r["p"], r["n"]) for r in rows]
    finally:
        if own:
            conn.close()


def get(entry_id: int, db_path: str = None, conn: sqlite3.Connection = None):
    own = conn is None
    conn = conn or connect(db_path)
    try:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        if own:
            conn.close()


def supersede(
    old_id: int, new_id: int, db_path: str = None, conn: sqlite3.Connection = None
) -> bool:
    """Mark *old_id* as corrected by *new_id*.

    Kept rather than deleting: knowing an answer was wrong, and what replaced it, is itself
    worth retrieving. The 146x figure that this store exists to prevent was a correction of a
    correction.
    """
    own = conn is None
    conn = conn or connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE entries SET superseded_by = ? WHERE id = ?", (new_id, old_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        if own:
            conn.close()


def find_duplicate(
    question: str, threshold: float = 0.6, db_path: str = None, conn: sqlite3.Connection = None
):
    """Return an existing entry that looks like the same question, or None.

    Word-overlap on the question only. Deliberately crude: its job is to prompt a supersede
    instead of silently adding a near-duplicate row, not to be clever.
    """
    hits = search(question, limit=3, db_path=db_path, conn=conn)
    if not hits:
        return None
    want = {w.lower() for w in _FTS_SAFE.findall(question) if len(w) > 2}
    if not want:
        return None
    for h in hits:
        have = {w.lower() for w in _FTS_SAFE.findall(h["question"]) if len(w) > 2}
        if have and len(want & have) / max(len(want), len(have)) >= threshold:
            return h
    return None


def stats(db_path: str = None, conn: sqlite3.Connection = None) -> Dict[str, Any]:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(source='explicit')   AS explicit,
                      SUM(source='extracted')  AS extracted,
                      SUM(superseded_by IS NOT NULL) AS superseded
               FROM entries"""
        ).fetchone()
        return {k: (row[k] or 0) for k in row.keys()}
    finally:
        if own:
            conn.close()
