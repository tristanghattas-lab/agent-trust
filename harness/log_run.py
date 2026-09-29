#!/usr/bin/env python3
"""
CLI for logging a manual threat-testing run against the task list.

This is how mode 2 gets its first data: you operate an agent surface
by hand (ChatGPT, Perplexity, a browser-use agent, Gemini) through a
task from harness/tasks.py, then log the result here. Once a pattern
of what's worth automating emerges, replace this loop with the
Playwright-driven runner — don't build that yet.

Usage (interactive):
    python -m harness.log_run

Usage (one-shot, scriptable):
    python -m harness.log_run --surface chatgpt --task age_verification \\
        --result fail --exploit --notes "Agent proceeded without asking DOB"
"""
import argparse
import sys

import requests

from harness.tasks import AGENT_SURFACES, TASKS

API_URL_DEFAULT = "http://localhost:8000/threat-runs"


def _task_by_key(key: str):
    for t in TASKS:
        if t.key == key:
            return t
    raise SystemExit(f"Unknown task key: {key}. Valid: {[t.key for t in TASKS]}")


def log_run(
    *,
    api_url: str,
    surface: str,
    task_key: str,
    result: str,
    exploit_found: bool,
    notes: str | None,
    tester: str | None,
) -> None:
    task = _task_by_key(task_key)
    payload = {
        "agent_surface": surface,
        "task_name": task.key,
        "task_category": task.category,
        "result": result,
        "exploit_found": exploit_found,
        "friction_notes": notes,
        "tester": tester,
    }
    resp = requests.post(api_url, json=payload, timeout=10)
    resp.raise_for_status()
    print(f"Logged: {surface} / {task.key} -> {result} (id={resp.json().get('id')})")


def interactive(api_url: str) -> None:
    print("Agent Trust — manual threat-test logger\n")
    print("Surfaces:", ", ".join(AGENT_SURFACES))
    surface = input("Agent surface: ").strip()

    print("\nTasks:")
    for t in TASKS:
        print(f"  {t.key:22s} [{t.category:10s}] {t.prompt}")
    task_key = input("\nTask key: ").strip()
    task = _task_by_key(task_key)
    print(f"\nExploit signal to watch for: {task.exploit_signal}")

    result = input("Result (pass/fail/partial): ").strip().lower()
    exploit_raw = input("Exploit found? (y/N): ").strip().lower()
    exploit_found = exploit_raw == "y"
    notes = input("Notes (what happened, verbatim if relevant): ").strip() or None
    tester = input("Tester (your name, optional): ").strip() or None

    log_run(
        api_url=api_url,
        surface=surface,
        task_key=task_key,
        result=result,
        exploit_found=exploit_found,
        notes=notes,
        tester=tester,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=API_URL_DEFAULT)
    parser.add_argument("--surface", choices=AGENT_SURFACES)
    parser.add_argument("--task", dest="task_key", choices=[t.key for t in TASKS])
    parser.add_argument("--result", choices=["pass", "fail", "partial"])
    parser.add_argument("--exploit", action="store_true", default=False)
    parser.add_argument("--notes")
    parser.add_argument("--tester")
    args = parser.parse_args()

    if args.surface and args.task_key and args.result:
        log_run(
            api_url=args.api_url,
            surface=args.surface,
            task_key=args.task_key,
            result=args.result,
            exploit_found=args.exploit,
            notes=args.notes,
            tester=args.tester,
        )
    else:
        interactive(args.api_url)


if __name__ == "__main__":
    sys.exit(main())
