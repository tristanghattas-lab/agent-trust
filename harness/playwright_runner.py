"""
STUB — not wired up yet.

Per the build sequence in the North Star doc: automate a task here only
once manual runs (harness/log_run.py) have shown which tests are worth
repeating. Don't fill this in before that.

The intended shape, when it's time:
  - drive a real browser-use-style agent loop (or record/replay a fixed
    script per task) with Playwright against the live or staging site
  - on completion, call the same /threat-runs endpoint log_run.py posts
    to, so results land in the same table without a separate schema
  - keep task definitions in harness/tasks.py as the single source of
    truth for both the manual and automated paths
"""

raise NotImplementedError(
    "Not built yet — run harness/log_run.py manually first. "
    "See the module docstring for when to fill this in."
)
