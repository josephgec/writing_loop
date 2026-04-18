#!/usr/bin/env python3
"""Writer <-> Editor self-improvement loop for iterative writing refinement."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


WRITER_SYSTEM_PROMPT = """You are a world-class writer. You produce vivid, compelling, well-structured prose.

RULES:
- When given a topic/prompt, write an excellent piece (essay, article, story — whatever fits the prompt).
- When given EDITOR FEEDBACK along with your previous draft, revise the draft to address every feedback point. Output ONLY the revised full text.
- Do NOT include meta-commentary like "Here is my revision" or "I've addressed the feedback". Just output the text.
- Aim for publication-quality work: strong openings, clear structure, varied sentence rhythm, precise word choice, satisfying conclusions.
- Output ONLY the written piece.
- If you are shown PRIOR FEEDBACK that has already been addressed, do NOT regress on those points when addressing new feedback."""

EDITOR_SYSTEM_PROMPT = """You are a ruthless but constructive editor at a top-tier publication.

Your job is to review a piece of writing and either APPROVE it or provide specific feedback for revision.

RESPONSE FORMAT:
- Always begin your response with a score line in this exact format:
  SCORE: N/10
  where N is your overall quality rating from 1 (unpublishable) to 10 (exceptional).
- After the score line, either provide feedback OR approval as described below.

RULES FOR FEEDBACK:
- Judge the piece on: clarity, structure, voice, word choice, sentence rhythm, opening strength, argument quality, redundancy, clichés, and overall impact.
- Provide 3-5 specific, actionable feedback points as a numbered list.
- Be concise but precise. Reference specific sentences or paragraphs when possible.
- Do NOT rewrite the piece yourself — only give editorial direction.
- Be demanding. Good enough is not enough.

RULES FOR APPROVAL:
- ONLY approve work that is genuinely publication-ready — polished, compelling, and complete.
- When approving, the line immediately after the SCORE line must be literally "APPROVED":
  SCORE: 10/10
  APPROVED
  [One sentence of praise explaining what makes it work]

Do not approve prematurely. Push for excellence."""


DEFAULT_LOG_DIR = Path(os.path.expanduser("~/.writing-loop/logs"))
CALL_TIMEOUT_SECONDS = 300
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]  # seconds — one delay per retry attempt
SCORE_RE = re.compile(r"^\s*SCORE:\s*(\d+)\s*/\s*10\s*$")


def generate_run_id() -> str:
    """Return a timestamp-based run id like '20260417_143022'."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def create_log_dir(base: Path, run_id: str) -> Path:
    run_dir = Path(base) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_log(log_dir: Path, filename: str, content: str) -> Path:
    path = Path(log_dir) / filename
    path.write_text(content, encoding="utf-8")
    return path


def parse_score(feedback: str) -> int | None:
    """Extract the score from a 'SCORE: N/10' line anywhere in the response."""
    if not feedback:
        return None
    for line in feedback.strip().splitlines():
        m = SCORE_RE.match(line)
        if m:
            return int(m.group(1))
    return None


def is_approved(feedback: str) -> bool:
    """True when the editor's response indicates approval.

    Approval format (SCORE line optional for backwards-compatibility):
        SCORE: N/10
        APPROVED
        ...praise...
    """
    if not feedback:
        return False
    for line in feedback.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if SCORE_RE.match(stripped):
            continue
        return stripped == "APPROVED"
    return False


def call_claude(
    user_prompt: str,
    system_prompt: str,
    model: str,
    retries: int = MAX_RETRIES,
) -> str:
    """Invoke `claude --print` and return its stdout, retrying on transient failures."""
    cmd = [
        "claude",
        "--print",
        "--model", model,
        "--system-prompt", system_prompt,
    ]
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                cmd,
                input=user_prompt,
                capture_output=True,
                text=True,
                timeout=CALL_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as e:
            # No point retrying — the CLI isn't installed.
            raise RuntimeError(
                "Could not find the `claude` CLI on PATH. Install Claude Code first: https://claude.com/claude-code"
            ) from e
        except subprocess.TimeoutExpired as e:
            last_error = e
        else:
            if result.returncode == 0:
                return result.stdout.strip()
            last_error = RuntimeError(
                f"Claude CLI exited with code {result.returncode}.\nstderr:\n{result.stderr}"
            )

        if attempt < retries:
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            print(f"\n  ⚠  Claude call failed (attempt {attempt + 1}/{retries + 1}): {last_error}")
            print(f"     Retrying in {delay}s...")
            time.sleep(delay)

    if isinstance(last_error, subprocess.TimeoutExpired):
        raise RuntimeError(f"Claude CLI timed out after {CALL_TIMEOUT_SECONDS}s") from last_error
    assert last_error is not None
    raise last_error


def save_final(draft: str, output_path: str | None, log_dir: Path) -> None:
    save_log(log_dir, "final_draft.md", draft)
    if output_path:
        Path(output_path).expanduser().write_text(draft, encoding="utf-8")


def build_writer_input(
    prompt: str,
    iteration: int,
    previous_draft: str | None,
    feedback_history: list[str],
) -> str:
    """Build the Writer's user message.

    `feedback_history` is the list of ALL editor feedback strings from prior iterations,
    in order. The Writer receives every prior round so it doesn't regress on earlier fixes.
    """
    if iteration == 1 or previous_draft is None or not feedback_history:
        return f"Write about the following:\n\n{prompt}"

    history_block = ""
    if len(feedback_history) > 1:
        past = "\n\n".join(
            f"--- Round {i + 1} feedback (already addressed) ---\n{fb}"
            for i, fb in enumerate(feedback_history[:-1])
        )
        history_block = (
            f"PRIOR editorial feedback (already addressed in previous revisions — "
            f"do NOT regress on these points):\n\n{past}\n\n"
        )

    return (
        f"Here is your previous draft:\n\n---\n{previous_draft}\n---\n\n"
        f"{history_block}"
        f"Here is the editor's LATEST feedback to address now:\n\n{feedback_history[-1]}\n\n"
        f"Revise the draft to address the latest feedback while preserving all prior improvements. "
        f"Output ONLY the revised text."
    )


def build_editor_input(prompt: str, draft: str, iteration: int) -> str:
    return (
        f"Please review the following piece:\n\n---\n{draft}\n---\n\n"
        f"Original prompt: \"{prompt}\"\n"
        f"This is revision #{iteration}."
    )


def run_loop(
    prompt: str,
    max_iterations: int,
    writer_model: str,
    editor_model: str,
    log_dir: Path,
    output_path: str | None,
    verbose: bool,
    input_draft: str | None = None,
) -> int:
    run_id = generate_run_id()
    run_log_dir = create_log_dir(log_dir, run_id)

    print("Writing Loop v1.1")
    print(f'Prompt: "{prompt}"')
    if input_draft is not None:
        print(f"Starting from input draft ({len(input_draft.split())} words) — iteration 1 will skip the Writer.")
    print(f"Max iterations: {max_iterations} | Writer: {writer_model} | Editor: {editor_model}")
    print(f"Logs: {run_log_dir}")

    save_log(run_log_dir, "prompt.md", prompt)
    if input_draft is not None:
        save_log(run_log_dir, "input_draft.md", input_draft)

    feedback_history: list[str] = []
    previous_draft: str | None = input_draft
    latest_draft: str | None = input_draft

    try:
        for i in range(1, max_iterations + 1):
            print()
            print("=" * 60)
            print(f"  Iteration {i}/{max_iterations}")
            print("=" * 60)

            # Writer phase — skip entirely on iteration 1 when an input draft is provided.
            if i == 1 and input_draft is not None:
                draft = input_draft
                print("  📄  Using provided input draft as iteration 1 starting point.")
            else:
                writer_input = build_writer_input(prompt, i, previous_draft, feedback_history)
                save_log(run_log_dir, f"iter{i:03d}_writer_input.md", writer_input)

                print(f"  ✍️  Writer is drafting (revision #{i})...", end="", flush=True)
                t0 = time.monotonic()
                draft = call_claude(writer_input, WRITER_SYSTEM_PROMPT, writer_model)
                word_count = len(draft.split())
                print(f"  done ({time.monotonic() - t0:.1f}s, {word_count} words)")

                save_log(run_log_dir, f"iter{i:03d}_writer_output.md", draft)
                latest_draft = draft
                if verbose:
                    print("\n--- Draft ---")
                    print(draft)
                    print("--- End Draft ---\n")

            # Editor phase
            editor_input = build_editor_input(prompt, draft, i)
            save_log(run_log_dir, f"iter{i:03d}_editor_input.md", editor_input)

            print(f"  🔍  Editor is reviewing draft #{i}...    ", end="", flush=True)
            t0 = time.monotonic()
            feedback = call_claude(editor_input, EDITOR_SYSTEM_PROMPT, editor_model)
            elapsed = time.monotonic() - t0

            score = parse_score(feedback)
            score_str = f"{score}/10" if score is not None else "?/10"
            approved = is_approved(feedback)
            status = "APPROVED ✓" if approved else "revisions requested"
            print(f"  done ({elapsed:.1f}s) — score {score_str} — {status}")

            save_log(run_log_dir, f"iter{i:03d}_editor_output.md", feedback)
            if verbose:
                print("\n--- Feedback ---")
                print(feedback)
                print("--- End Feedback ---\n")

            if approved:
                print(f"\n  ✅ Editor APPROVED the draft after {i} iteration(s)!")
                save_log(run_log_dir, f"iter{i:03d}_APPROVED.md", f"# Approved\n\n{feedback}")
                save_final(draft, output_path, run_log_dir)
                _print_outputs(output_path, run_log_dir)
                return 0

            feedback_history.append(feedback)
            previous_draft = draft
            latest_draft = draft

        print(f"\n  ⚠️  Max iterations ({max_iterations}) reached without approval.")
        print("  Saving latest draft as best effort.")
        save_final(latest_draft or "", output_path, run_log_dir)
        _print_outputs(output_path, run_log_dir)
        return 2

    except KeyboardInterrupt:
        print("\n\n  ⏹  Interrupted by user. Saving latest draft...")
        if latest_draft is not None:
            save_final(latest_draft, output_path, run_log_dir)
            _print_outputs(output_path, run_log_dir)
        else:
            print("  (No draft produced yet.)")
        return 130


def _print_outputs(output_path: str | None, run_log_dir: Path) -> None:
    if output_path:
        print(f"\nFinal draft saved to: {output_path}")
    print(f"Full logs: {run_log_dir}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="writing_loop",
        description="Run a Writer/Editor feedback loop to iteratively improve a piece of writing.",
    )
    p.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="The writing topic/instructions (pass '-' to read from stdin; optional if --prompt-file or --input-draft is given)",
    )
    p.add_argument("--prompt-file", default=None, help="Read the prompt from this file")
    p.add_argument(
        "--input-draft",
        default=None,
        help="Start from an existing draft file — iteration 1 skips the Writer and sends the draft straight to the Editor",
    )
    p.add_argument("--max-iterations", type=int, default=5, help="Maximum number of write/edit cycles (default: 5)")
    p.add_argument("--writer-model", default="sonnet", help="Model for the writer role (default: sonnet)")
    p.add_argument("--editor-model", default="sonnet", help="Model for the editor role (default: sonnet)")
    p.add_argument("--output", default=None, help="Save the final draft to this file path")
    p.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR), help="Directory for run logs (default: ~/.writing-loop/logs)")
    p.add_argument("--verbose", action="store_true", help="Print full drafts and feedback to the terminal")
    return p


def resolve_prompt(args: argparse.Namespace, stdin_reader=sys.stdin) -> str | None:
    """Resolve the prompt from --prompt-file, stdin (when prompt == '-'), or the positional arg."""
    if args.prompt_file:
        return Path(args.prompt_file).expanduser().read_text(encoding="utf-8").strip()
    if args.prompt == "-":
        return stdin_reader.read().strip()
    return args.prompt


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.max_iterations < 1:
        parser.error("--max-iterations must be >= 1")

    prompt = resolve_prompt(args)
    input_draft = None
    if args.input_draft:
        input_draft = Path(args.input_draft).expanduser().read_text(encoding="utf-8").strip()

    if prompt is None and input_draft is None:
        parser.error("must provide a prompt, --prompt-file, or --input-draft")

    if prompt is None:
        prompt = "Polish and improve this existing piece of writing."

    return run_loop(
        prompt=prompt,
        max_iterations=args.max_iterations,
        writer_model=args.writer_model,
        editor_model=args.editor_model,
        log_dir=Path(args.log_dir).expanduser(),
        output_path=args.output,
        verbose=args.verbose,
        input_draft=input_draft,
    )


if __name__ == "__main__":
    sys.exit(main())
