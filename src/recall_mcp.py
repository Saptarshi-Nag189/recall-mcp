#!/usr/bin/env python3
"""
recall_mcp — MCP server exposing the recall store to any MCP-speaking client.

Registered alongside the four @modelcontextprotocol/server-memory banks. Those hold durable
facts; this holds episodes - the question, the answer, and the raw evidence behind it.

Speaks MCP over stdio with no dependencies beyond the stdlib, so Claude Code, Codex,
Antigravity or anything else can mount the same database file. Nothing is auto-injected into
context: the tools are pull-only, unlike the session-start hook which spends tokens on every
prompt.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recall_store  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"

# ── write authorisation ──────────────────────────────────────────────────────
#
# Every CLI on this machine runs as the same OS user, so file permissions cannot tell one
# client from another - anything one can write, all can. The gate is therefore a shared secret
# carried in the MCP server's own env block: only the client whose config sets RECALL_WRITE_KEY
# launches this process with write access. Codex and agy read and search freely; they cannot
# add, correct or delete.
#
# This is a guard rail, not a security boundary. Anyone who can read ~/.claude.json can read the
# key, and the store is a plain file underneath. It stops an agent from casually rewriting
# curated knowledge; it does not stop a determined one. Making that explicit matters more than
# implying a protection that is not there.
#
# The audit log is the half that always works: every write attempt, allowed or refused, is
# recorded with the client that made it, so an unexpected change is visible after the fact.
_EXPECTED_KEY = os.environ.get("RECALL_WRITE_KEY", "")
_CLIENT = os.environ.get("RECALL_CLIENT", "unknown")
AUDIT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recall_audit.log")

WRITE_TOOLS = {"recall_add", "recall_supersede"}


def _writes_allowed() -> bool:
    """True when this process was launched by a client holding the write key."""
    return bool(_EXPECTED_KEY)


def _audit(action: str, detail: str, allowed: bool) -> None:
    """Append one line per write attempt. Never raises - logging must not break a call."""
    try:
        with open(AUDIT_LOG, "a") as fh:
            fh.write("%s\t%s\t%s\t%s\t%s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                _CLIENT,
                "ALLOW" if allowed else "REFUSE",
                action,
                detail.replace("\t", " ").replace("\n", " ")[:200],
            ))
    except Exception:  # noqa: BLE001
        pass

TOOLS = [
    {
        "name": "recall_search",
        "description": (
            "Look up an answer already worked out in a previous session, with the raw evidence "
            "behind it. USE THIS BEFORE re-deriving, re-measuring or re-investigating anything "
            "that sounds like it has been done before - questions about how much a change "
            "gained, what a past measurement was, why an approach was rejected, or what a "
            "previous decision settled on. Cheaper and more reliable than re-running a "
            "benchmark, which can be constructed differently and give a different answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords or the question itself. Keyword-based, not semantic.",
                },
                "limit": {"type": "integer", "description": "Max results (default 5)"},
                "include_superseded": {
                    "type": "boolean",
                    "description": "Include entries later corrected (default false)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "recall_add",
        "description": (
            "Record a question and its answer with the evidence that backs it, so it can be "
            "looked up later instead of re-derived. Use after establishing something measurable "
            "or non-obvious: a benchmark result, a root cause, a rejected approach and why. "
            "Put raw numbers verbatim in evidence - a remembered figure being wrong is the "
            "failure this store exists to prevent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "answer": {"type": "string", "description": "The conclusion, concise"},
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Raw measurement lines, verbatim",
                },
                "refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Commit shas, file:line references",
                },
                "tags": {"type": "string", "description": "Space-separated keywords"},
                "project": {"type": "string"},
                "supersedes": {
                    "type": "integer",
                    "description": "Id of an entry this corrects",
                },
            },
            "required": ["question", "answer"],
        },
    },
    {
        "name": "recall_get",
        "description": "Fetch one entry in full by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "recall_supersede",
        "description": (
            "Mark an entry as corrected by a newer one, so the stale answer stops being served. "
            "Use whenever a previously recorded figure or conclusion turns out to be wrong."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"old_id": {"type": "integer"}, "new_id": {"type": "integer"}},
            "required": ["old_id", "new_id"],
        },
    },
]


def _fmt(entries):
    if not entries:
        return "No matching entry. Nothing has been recorded for this yet."
    out = []
    for e in entries:
        head = "#%s (%s)" % (e["id"], e.get("created_at", "")[:10])
        if not e.get("confidence", 1):
            head += " [auto-extracted, lower confidence]"
        if e.get("superseded_by"):
            head += " [SUPERSEDED by #%s]" % e["superseded_by"]
        block = [head, "Q: " + e["question"], "A: " + e["answer"]]
        if e.get("evidence"):
            block.append("Evidence:")
            block += ["  " + line for line in e["evidence"]]
        if e.get("refs"):
            block.append("Refs: " + ", ".join(e["refs"]))
        out.append("\n".join(block))
    return "\n\n".join(out)


def handle(method, params, req_id):
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "recall", "version": "1.0.0"},
        }
    if method == "tools/list":
        # A read-only client is not shown the write tools at all. Advertising a tool that always
        # refuses invites an agent to keep retrying it and to report a failure it cannot fix.
        if _writes_allowed():
            return {"tools": TOOLS}
        return {"tools": [t for t in TOOLS if t["name"] not in WRITE_TOOLS]}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}

        if name in WRITE_TOOLS and not _writes_allowed():
            _audit(name, str(args.get("question", ""))[:120], allowed=False)
            return {"content": [{"type": "text", "text":
                    "This client has read-only access to the recall store. Search and read are "
                    "available; adding or correcting entries is not. Ask the user to record it, "
                    "or use the CLI: recall add -q '...' -a '...'"}],
                    "isError": True}
        try:
            if name == "recall_search":
                hits = recall_store.search(
                    args.get("query", ""),
                    limit=int(args.get("limit") or 5),
                    include_superseded=bool(args.get("include_superseded")),
                )
                text = _fmt(hits)
            elif name == "recall_add":
                dup = recall_store.find_duplicate(args.get("question", ""))
                new_id = recall_store.add(
                    question=args.get("question", ""),
                    answer=args.get("answer", ""),
                    evidence=args.get("evidence"),
                    refs=args.get("refs"),
                    tags=args.get("tags", ""),
                    project=args.get("project", ""),
                    source="explicit",
                    confidence=1,
                )
                if args.get("supersedes"):
                    recall_store.supersede(int(args["supersedes"]), new_id)
                _audit("recall_add", args.get("question", "")[:120], allowed=True)
                text = "Stored as #%d." % new_id
                if dup and not args.get("supersedes"):
                    text += (
                        " Note: #%s covers a similar question - consider recall_supersede "
                        "if this corrects it." % dup["id"]
                    )
            elif name == "recall_get":
                e = recall_store.get(int(args.get("id", 0)))
                text = _fmt([e] if e else [])
            elif name == "recall_supersede":
                _audit("recall_supersede",
                       "#%s -> #%s" % (args.get("old_id"), args.get("new_id")), allowed=True)
                ok = recall_store.supersede(int(args["old_id"]), int(args["new_id"]))
                text = "Marked." if ok else "No such entry."
            else:
                return {"error": {"code": -32601, "message": "unknown tool: %s" % name}}
            return {"content": [{"type": "text", "text": text}]}
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": "recall error: %s" % exc}],
                    "isError": True}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        result = handle(req.get("method"), req.get("params") or {}, req.get("id"))
        if req.get("id") is None:
            continue  # notification: no response expected
        resp = {"jsonrpc": "2.0", "id": req.get("id")}
        if isinstance(result, dict) and "error" in result:
            resp["error"] = result["error"]
        else:
            resp["result"] = result if result is not None else {}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
