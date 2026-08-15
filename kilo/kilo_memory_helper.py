#!/usr/bin/env python3
# kilo_memory_helper.py
# Python helper to append entities to shared memory banks.
# Usage:
#   python3 kilo_memory_helper.py --bank project --type ModelSession --name "Kilo_Session_..." --obs "..." --obs "..."

import json
import argparse
import sys
import os
from datetime import datetime

BANKS = {
    "global": os.path.expanduser("~/.shared_memory/global.json"),
    "project": os.path.expanduser("~/CDAC_Projects/IOT_security/.shared_memory/project.json"),
}

def write_entity(bank_path: str, entity: dict):
    """Append a single entity as JSONL line to the bank file."""
    with open(bank_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entity, ensure_ascii=False) + "\n")

def make_model_session(name: str, observations: list) -> dict:
    return {
        "type": "entity",
        "name": name,
        "entityType": "ModelSession",
        "observations": observations
    }

def make_session_summary(name: str, observations: list) -> dict:
    return {
        "type": "entity",
        "name": name,
        "entityType": "SessionSummary",
        "observations": observations
    }

def make_generic_entity(name: str, entity_type: str, observations: list) -> dict:
    return {
        "type": "entity",
        "name": name,
        "entityType": entity_type,
        "observations": observations
    }

def main():
    ap = argparse.ArgumentParser(description="Append entities to shared memory banks")
    ap.add_argument("--bank", choices=["global", "project"], required=True, help="Which bank to write to")
    ap.add_argument("--type", choices=["ModelSession", "SessionSummary", "DesignDecision", "Trap", "CrossRepoFact", "CoreComponent", "SecurityFlaw", "RepoFact", "ProjectState", "SystemConfig"], required=True, help="Entity type")
    ap.add_argument("--name", required=True, help="Entity name")
    ap.add_argument("--obs", action="append", required=True, help="Observation (repeatable)")
    ap.add_argument("--model", help="Model identifier (for ModelSession)")
    ap.add_argument("--provider", help="Provider (for ModelSession)")
    ap.add_argument("--tree", help="Working tree (for ModelSession)")
    ap.add_argument("--branch", help="Git branch (for ModelSession)")
    ap.add_argument("--handoff-from", help="Previous agent/session (for ModelSession)")
    args = ap.parse_args()

    bank_path = BANKS[args.bank]
    if not os.path.exists(bank_path):
        print(f"Bank not found: {bank_path}", file=sys.stderr)
        sys.exit(1)

    now = datetime.now().isoformat()
    observations = list(args.obs)

    if args.type == "ModelSession":
        # Build standard ModelSession observations
        model_obs = [
            f"Model: {args.model or 'nemotron-3-ultra-550b-a55b:free'} (nemotron 3 ultra high)",
            f"Provider: {args.provider or 'kilo / nvidia'}",
            f"Session start: {now}",
            f"Session end: {now}",
            f"Working tree: {args.tree or os.path.basename(os.getcwd())} (branch {args.branch or 'unknown'})",
        ]
        if args.handoff_from:
            model_obs.append(f"Handoff from: {args.handoff_from}")
        model_obs.extend(observations)
        entity = make_model_session(args.name, model_obs)
    elif args.type == "SessionSummary":
        entity = make_session_summary(args.name, observations)
    else:
        entity = make_generic_entity(args.name, args.type, observations)

    write_entity(bank_path, entity)
    print(f"Written to {args.bank}: {args.name} [{args.type}]")

if __name__ == "__main__":
    main()