# Labelled agent runs

`labelled_runs.csv` is ground truth: one row per test where we know which
agent drove the browser. Each row names the session key, so the session's
features (behaviour counts, journey, checkout timings) can be joined back
for evaluating rules now and training a model later.

Add a row after every agent test: date, agent, session key and order from
the Run report, and what caught it.
