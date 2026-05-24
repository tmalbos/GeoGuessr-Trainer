---
name: bundle
description: >
  Orchestrates fixing multiple code review findings in parallel. Use this skill whenever the user pastes a code review report with numbered findings and wants them fixed — especially when the word "parallel", "subagents", or "fix all of these" appears. Also trigger when the user pastes a review with sections like Standards / Spec / Summary and asks to act on it. The skill parses the findings, figures out which ones can run in parallel vs. which ones must be sequential (same file or logical dependency), launches subagents to fix them, and then verifies the result against the original spec.
---

# fixbot

Reads a code review report, plans the fix order, dispatches subagents, verifies the result.

## What you need from the user

1. **The review** — numbered findings, one per issue. The format from the example has `Standards`, `Spec`, and `Summary` sections separated by `---`, but any numbered list works.
2. **The spec** — the acceptance criteria to verify against at the end. Usually an issue description or a list of "done when..." statements. Ask for it explicitly if it's not in the review.

---

## Step 1 — Parse findings

Extract every finding into a structured list:

```
finding_id: F1, F2, ...
file: which file(s) it touches (parse from the finding text — look for paths like src/foo/bar.py)
description: one-sentence summary of what's wrong and what the fix is
depends_on: [] (fill in next step)
```

If a finding doesn't name a specific file, label it `file: none` — these are usually architectural or test-coverage issues.

---

## Step 2 — Plan parallelism

Apply these rules in order:

1. **Same file → sequential.** If two findings touch the same file, the later one must wait for the earlier one. Pick the order that makes logical sense (e.g. fix the type inconsistency before the shim that relies on it).
2. **Logical dependency → sequential.** If fixing F2 requires F1's changes to exist first (e.g. F1 introduces a shared client that F2 then injects), mark `F2.depends_on = [F1]`.
3. **Everything else → parallel.**

Output a plan like this before doing anything:

```
PARALLEL BATCH 1: F1, F3, F4
  F1 — src/anki/cards.py — restore module-level cache
  F3 — src/anki/anki_connect.py — remove shim client, inject via DI
  F4 — src/geo_enrich.py — set User-Agent header on shared client

SEQUENTIAL (after batch 1):
  F2 — (depends on F3) — fix FlagNote to accept injected client
  F5 — tests/ — add integration test for AppContext wiring
```

Show this plan to the user and ask for a quick thumbs-up before proceeding. If they say go, proceed — don't wait for explicit confirmation wording.

---

## Step 3 — Launch subagents

For each batch, spawn one subagent per finding using the `Task` tool.

Each subagent prompt should include:
- The finding description
- The file(s) to touch
- Any context about what a previous batch already changed (if sequential)
- This instruction: **"Fix only what this finding describes. Do not refactor unrelated code. Do not change tests unless the finding explicitly requires it."**

For sequential batches, wait for the previous batch to finish before spawning the next one.

Do not implement the fixes yourself. Your job is orchestration.

---

## Step 4 — Verify against spec

Once all subagents are done, spawn a final verification subagent with this prompt:

```
You are a code reviewer. Your job is to check whether a set of findings have been properly fixed.

ORIGINAL SPEC:
<paste the spec the user provided>

FINDINGS THAT WERE FIXED:
<paste the full finding list>

Read the relevant files and verify:
1. Each finding is resolved
2. No new issues were introduced that contradict the spec
3. The spec's acceptance criteria are met

Report: PASS or FAIL per finding, then an overall verdict.
```

Report the verification result to the user. If anything fails, surface the specific finding ID and what's still wrong — don't re-fix automatically, let the user decide whether to re-run fixbot or handle it manually.

---

## Edge cases

- **Finding touches no file** (e.g. "add a test"): treat as `file: tests/` and put it in the last sequential batch so it can validate what was already fixed.
- **Ambiguous dependency**: when unsure, make it sequential. Safer than a race condition on the same file.
- **User says "skip F2"**: remove it from the plan, note it in the final summary as skipped.
- **Verification subagent can't find a file**: report it — don't guess.