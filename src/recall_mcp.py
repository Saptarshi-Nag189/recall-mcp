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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recall_store  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"

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
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
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
