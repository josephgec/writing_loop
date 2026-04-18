# writing-loop

A tiny CLI that pits two Claude instances against each other — a **Writer** and an **Editor** — in a feedback loop until the editor declares the piece publication-ready.

Given a topic, the Writer produces a draft. The Editor either approves it or returns specific, actionable notes. The Writer revises. Repeat until the Editor says `APPROVED` (or the iteration cap is hit).

It shells out to the `claude` CLI (`--print` mode), so it uses your existing Claude Code subscription — no API key needed.

## Architecture

```
                    +------------------+
                    |   User provides  |
                    |  topic + config  |
                    +--------+---------+
                             |
                             v
                    +------------------+
                    |   Orchestrator   |
                    |   (writing_loop) |
                    +--------+---------+
                             |
               +-------------+-------------+
               |                           |
               v                           v
      +-----------------+        +------------------+
      |     Writer      |        |     Editor       |
      | (claude --print |        | (claude --print  |
      |  --system-prompt|        |  --system-prompt |
      |  "writer...")   |        |  "editor...")    |
      +-----------------+        +------------------+
               |                           |
               |      Draft text           |
               +----------->---------------+
               |                           |
               |   Feedback OR "APPROVED"  |
               +-----------<---------------+
               |                           |
               v                           |
      +------------------+                 |
      |   "APPROVED"?    |----no---------->+
      +--------+---------+
               | yes
               v
            [Done — final draft saved]
```

Each call to Claude is independent and self-contained — no conversation history carries over. The Writer receives the previous draft and the Editor's feedback inline; the Editor receives the new draft plus the original prompt. This keeps prompts reproducible and easy to log.

## Install

Requirements:
- Python 3.9+
- [Claude Code CLI](https://claude.com/claude-code) installed and authenticated (`claude` on your `$PATH`)

Clone and run — no dependencies beyond the standard library.

```bash
git clone https://github.com/josephgec/writing_loop.git
cd writing_loop
python3 writing_loop.py --help
```

Or install as a proper command on your `$PATH`:

```bash
pip install .
writing-loop --help
```

## Usage

```bash
python3 writing_loop.py "Write a 500-word essay on why curiosity is humanity's greatest trait"
```

Longer prompts from a file:

```bash
python3 writing_loop.py --prompt-file brief.md --output final.md
```

Polish an existing draft instead of writing from scratch:

```bash
python3 writing_loop.py "The piece is a blog post about X" \
    --input-draft my-draft.md \
    --output polished.md
```

Prompt via stdin (useful for pipelines):

```bash
echo "Write a tweet about curiosity" | python3 writing_loop.py -
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `prompt` | conditionally | The writing topic/instructions. Pass `-` to read from stdin. Optional if `--prompt-file` or `--input-draft` is given. |

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--prompt-file PATH` | _(none)_ | Read the prompt from a file instead of passing it inline |
| `--input-draft PATH` | _(none)_ | Start from an existing draft — iteration 1 skips the Writer and sends your draft straight to the Editor |
| `--max-iterations N` | `5` | Maximum number of write/edit cycles |
| `--writer-model NAME` | `sonnet` | Model alias for the Writer (`sonnet`, `opus`, `haiku`, or a full model ID) |
| `--editor-model NAME` | `sonnet` | Model alias for the Editor |
| `--style STYLE` | `default` | Writing style preset: `default`, `academic`, `journalistic`, `fiction`, `technical`, `blog`, `persuasive` |
| `--strictness LEVEL` | `standard` | Editor severity: `lenient`, `standard`, `harsh` |
| `--target-words N` | _(none)_ | Target word count — Editor factors length compliance into its score |
| `--approve-above N` | _(none)_ | Accept any draft scoring ≥ N/10 (in addition to literal `APPROVED`) |
| `--plateau-window N` | `3` | Stop early if the last N scores are identical (`0` disables) |
| `--format FMT` | `md` | Output format for `--output`: `md`, `txt`, `html` |
| `--output PATH` | _(none)_ | Save the final draft to this path (always also saved in the log dir) |
| `--log-dir DIR` | `~/.writing-loop/logs` | Where per-run log directories are created |
| `--verbose` | off | Print full drafts and feedback inline |
| `--quiet` | off | Suppress progress output |
| `--json` | off | Emit a structured JSON summary to stdout (implies `--quiet`) |

## How the Editor rates each draft

The Editor is required to begin every response with a score line:

```
SCORE: 7/10
1. The opening hook is weak...
2. Paragraph 3 has redundancy...
```

The orchestrator parses this and shows per-iteration progress:

```
  🔍  Editor is reviewing draft #3...      done (1.1s) — score 7/10 — revisions requested
  🔍  Editor is reviewing draft #4...      done (0.9s) — score 10/10 — APPROVED ✓
```

## Accumulated feedback history

On every revision, the Writer receives **all prior editor feedback**, not just the most recent round — so it doesn't regress on issues that earlier rounds already fixed. Earlier rounds are labeled `already addressed` to signal "preserve these improvements"; the latest round is labeled `LATEST feedback to address now`.

## Reliability: retries with backoff

`call_claude` automatically retries transient failures (non-zero exits, timeouts) with exponential backoff: 5s → 15s → 30s, up to 3 retries. A missing `claude` CLI is not retried — it fails immediately with an install hint.

## Plateau detection

If the Editor returns the same score for `--plateau-window` iterations in a row (default: 3), the loop stops early and exits with code `3` — the draft has converged and more iterations are unlikely to help.

## JSON mode (for scripting / CI)

`--json` emits a structured summary to stdout instead of human-readable progress:

```json
{
  "status": "approved",
  "iterations": 3,
  "scores": [4, 7, 10],
  "log_dir": "/Users/you/.writing-loop/logs/20260417_143022",
  "output_path": "final.md",
  "output_format": "md",
  "final_draft": "..."
}
```

`status` is one of `approved`, `max_iterations`, `plateau`, or `interrupted`. This plays well with CI writing-quality gates: `writing-loop --json ... | jq '.scores[-1]'`.

## Example session

```
Writing Loop v1.0
Prompt: "Write a 500-word essay on why curiosity is humanity's greatest trait"
Max iterations: 5 | Writer: sonnet | Editor: sonnet
Logs: /Users/you/.writing-loop/logs/20260417_143022/

============================================================
  Iteration 1/5
============================================================
  ✍️  Writer is drafting (revision #1)...  done (1.8s)
  🔍  Editor is reviewing draft #1...      done (1.2s)
  📝  Editor requested revisions. Continuing...

============================================================
  Iteration 2/5
============================================================
  ✍️  Writer is drafting (revision #2)...  done (2.1s)
  🔍  Editor is reviewing draft #2...      done (1.0s)

  ✅ Editor APPROVED the draft after 2 iteration(s)!

Final draft saved to: final_essay.md
Full logs: /Users/you/.writing-loop/logs/20260417_143022/
```

## Logs

Every call in and out of Claude is written to disk, so you can inspect exactly what was asked and what came back:

```
~/.writing-loop/logs/20260417_143022/
├── prompt.md
├── iter001_writer_input.md
├── iter001_writer_output.md
├── iter001_editor_input.md
├── iter001_editor_output.md
├── iter002_writer_input.md
├── iter002_writer_output.md
├── iter002_editor_input.md
├── iter002_editor_output.md
├── iter002_APPROVED.md     # only present if the editor approved
└── final_draft.md
```

## How the Editor decides

The Editor reviews along ten dimensions (clarity, structure, voice, word choice, sentence rhythm, opening strength, argument quality, redundancy, clichés, overall impact) and returns 3–5 numbered, actionable notes, preceded by a `SCORE: N/10` line.

The Editor also receives its own prior feedback each round, so it can verify that earlier notes were addressed and avoid repeating itself.

Approval triggers:
1. The Editor's verdict line contains `APPROVED` (tolerant to phrasings like `APPROVED — great work`, `APPROVED: strong piece`, or `Overall: APPROVED`). Negations (`NOT APPROVED`) and numbered feedback items are rejected.
2. **Or** the score is `>= --approve-above N` if that flag is set.

If the iteration cap or plateau is reached first, the most recent draft is saved as best-effort.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Editor approved |
| `2` | Max iterations hit without approval |
| `3` | Plateau detected — scores stopped improving |
| `130` | User interrupted (`Ctrl+C`) |

## Development

### Run the tests

```bash
python3 -m unittest discover tests
```

Tests mock `subprocess.run`, so they never actually call the Claude CLI and don't require `claude` to be installed.

### Coverage

```bash
pip install coverage
python3 -m coverage run --source=writing_loop -m unittest discover tests
python3 -m coverage report -m
```

Current coverage is **99%** (103 tests) — the only uncovered line is the `if __name__ == "__main__"` entry guard.

### Project layout

```
writing_loop/
├── writing_loop.py          # Main orchestrator (all logic lives here)
├── pyproject.toml           # pip-installable package metadata
├── README.md
├── .gitignore
└── tests/
    └── test_writing_loop.py # Unit + integration tests (subprocess mocked)
```

## Design notes

- **Single file.** All orchestration lives in `writing_loop.py`. No frameworks, no plugins.
- **No hidden state between calls.** Each Claude invocation is a fresh `--print` call with an explicit user message. What you see in the log files is exactly what the model saw.
- **Symmetric feedback.** Both the Writer and the Editor receive their own accumulated history — the Writer to avoid regressing on earlier fixes, the Editor to avoid contradicting itself.
- **Tolerant approval matching.** The loop accepts many natural phrasings of approval (`APPROVED`, `APPROVED — excellent`, `Overall: APPROVED`) while rejecting negations and feedback items. The optional `--approve-above N` threshold adds a numerical escape hatch.
- **Logs are the source of truth.** If a run looks off, open the log directory.
