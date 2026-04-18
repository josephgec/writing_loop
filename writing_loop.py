#!/usr/bin/env python3
"""Writer <-> Editor self-improvement loop for iterative writing refinement."""

from __future__ import annotations

import argparse
import os
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
- Output ONLY the written piece."""

EDITOR_SYSTEM_PROMPT = """You are a ruthless but constructive editor at a top-tier publication.

Your job is to review a piece of writing and either APPROVE it or provide specific feedback for revision.

RULES FOR FEEDBACK:
- Judge the piece on: clarity, structure, voice, word choice, sentence rhythm, opening strength, argument quality, redundancy, clichés, and overall impact.
- Provide 3-5 specific, actionable feedback points as a numbered list.
- Be concise but precise. Reference specific sentences or paragraphs when possible.
- Do NOT rewrite the piece yourself — only give editorial direction.
- Be demanding. Good enough is not enough.

RULES FOR APPROVAL:
- ONLY approve work that is genuinely publication-ready — polished, compelling, and complete.
- When approving, respond with EXACTLY this format (first line must be literally "APPROVED"):
  APPROVED
  [One sentence of praise explaining what makes it work]

Do not approve prematurely. Push for excellence."""


DEFAULT_LOG_DIR = Path(os.path.expanduser("~/.writing-loop/logs"))
CALL_TIMEOUT_SECONDS = 300


def generate_run_id() -> str:
    """Return a timestamp-based run id like '20260417_143022'."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def create_log_dir(base: Path, run_id: str) -> Path:
    """Create and return base/run_id/ as a Path."""
    run_dir = Path(base) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_log(log_dir: Path, filename: str, content: str) -> Path:
    path = Path(log_dir) / filename
    path.write_text(content, encoding="utf-8")
    return path


def is_approved(feedback: str) -> bool:
    """True when the editor's response begins with the literal token 'APPROVED'."""
    if not feedback:
        return False
    first_line = feedback.strip().splitlines()[0].strip()
    return first_line == "APPROVED"


def call_claude(user_prompt: str, system_prompt: str, model: str) -> str:
    """Invoke `claude --print` and return its stdout."""
    cmd = [
        "claude",
        "--print",
        "--model", model,
        "--system-prompt", system_prompt,
    ]
    try:
        result = subprocess.run(
            cmd,
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=CALL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "Could not find the `claude` CLI on PATH. Install Claude Code first: https://claude.com/claude-code"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Claude CLI timed out after {CALL_TIMEOUT_SECONDS}s") from e

    if result.returncode != 0:
        raise RuntimeError(
            f"Claude CLI exited with code {result.returncode}.\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def save_final(draft: str, output_path: str | None, log_dir: Path) -> None:
    save_log(log_dir, "final_draft.md", draft)
    if output_path:
        Path(output_path).expanduser().write_text(draft, encoding="utf-8")


def build_writer_input(prompt: str, iteration: int, previous_draft: str | None, previous_feedback: str | None) -> str:
    if iteration == 1 or previous_draft is None:
        return f"Write about the following:\n\n{prompt}"
    return (
        f"Here is your previous draft:\n\n---\n{previous_draft}\n---\n\n"
        f"Here is the editor's feedback:\n\n{previous_feedback}\n\n"
        f"Revise the draft to address all feedback. Output ONLY the revised text."
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
) -> int:
    run_id = generate_run_id()
    run_log_dir = create_log_dir(log_dir, run_id)

    print("Writing Loop v1.0")
    print(f'Prompt: "{prompt}"')
    print(f"Max iterations: {max_iterations} | Writer: {writer_model} | Editor: {editor_model}")
    print(f"Logs: {run_log_dir}")

    save_log(run_log_dir, "prompt.md", prompt)

    previous_draft: str | None = None
    previous_feedback: str | None = None
    latest_draft: str | None = None

    try:
        for i in range(1, max_iterations + 1):
            print()
            print("=" * 60)
            print(f"  Iteration {i}/{max_iterations}")
            print("=" * 60)

            # Writer phase
            writer_input = build_writer_input(prompt, i, previous_draft, previous_feedback)
            save_log(run_log_dir, f"iter{i:03d}_writer_input.md", writer_input)

            print(f"  ✍️  Writer is drafting (revision #{i})...", end="", flush=True)
            t0 = time.monotonic()
            draft = call_claude(writer_input, WRITER_SYSTEM_PROMPT, writer_model)
            print(f"  done ({time.monotonic() - t0:.1f}s)")

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
            print(f"  done ({time.monotonic() - t0:.1f}s)")

            save_log(run_log_dir, f"iter{i:03d}_editor_output.md", feedback)
            if verbose:
                print("\n--- Feedback ---")
                print(feedback)
                print("--- End Feedback ---\n")

            if is_approved(feedback):
                print(f"\n  ✅ Editor APPROVED the draft after {i} iteration(s)!")
                save_log(run_log_dir, f"iter{i:03d}_APPROVED.md", f"# Approved\n\n{feedback}")
                save_final(draft, output_path, run_log_dir)
                _print_outputs(output_path, run_log_dir)
                return 0

            print("  📝  Editor requested revisions. Continuing...")
            previous_draft = draft
            previous_feedback = feedback

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
    p.add_argument("prompt", help="The writing topic/instructions")
    p.add_argument("--max-iterations", type=int, default=5, help="Maximum number of write/edit cycles (default: 5)")
    p.add_argument("--writer-model", default="sonnet", help="Model for the writer role (default: sonnet)")
    p.add_argument("--editor-model", default="sonnet", help="Model for the editor role (default: sonnet)")
    p.add_argument("--output", default=None, help="Save the final draft to this file path")
    p.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR), help="Directory for run logs (default: ~/.writing-loop/logs)")
    p.add_argument("--verbose", action="store_true", help="Print full drafts and feedback to the terminal")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.max_iterations < 1:
        parser.error("--max-iterations must be >= 1")

    return run_loop(
        prompt=args.prompt,
        max_iterations=args.max_iterations,
        writer_model=args.writer_model,
        editor_model=args.editor_model,
        log_dir=Path(args.log_dir).expanduser(),
        output_path=args.output,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
