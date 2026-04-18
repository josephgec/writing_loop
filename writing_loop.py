#!/usr/bin/env python3
"""Writer <-> Editor self-improvement loop for iterative writing refinement."""

from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


BASE_WRITER_SYSTEM_PROMPT = """You are a world-class writer. You produce vivid, compelling, well-structured prose.

RULES:
- When given a topic/prompt, write an excellent piece (essay, article, story — whatever fits the prompt).
- When given EDITOR FEEDBACK along with your previous draft, revise the draft to address every feedback point. Output ONLY the revised full text.
- Do NOT include meta-commentary like "Here is my revision" or "I've addressed the feedback". Just output the text.
- Aim for publication-quality work: strong openings, clear structure, varied sentence rhythm, precise word choice, satisfying conclusions.
- Output ONLY the written piece.
- If you are shown PRIOR FEEDBACK that has already been addressed, do NOT regress on those points when addressing new feedback."""

BASE_EDITOR_SYSTEM_PROMPT = """You are a ruthless but constructive editor at a top-tier publication.

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

RULES FOR APPROVAL:
- When approving, the line immediately after the SCORE line must begin with the word "APPROVED":
  SCORE: 10/10
  APPROVED
  [One sentence of praise explaining what makes it work]"""


STYLE_ADDONS: dict[str, str] = {
    "default": "",
    "academic": "Use formal academic tone. Cite concepts precisely. Avoid colloquialisms and first-person anecdotes.",
    "journalistic": "Write in AP style. Lead with the most important information. Use short paragraphs and concrete facts.",
    "fiction": "Prioritize showing over telling. Use sensory detail and scene-building. Build tension toward a payoff.",
    "technical": "Be precise and unambiguous. Use correct terminology. Prefer structure and examples over flourishes.",
    "blog": "Conversational tone. Personal anecdotes welcome. Break up text with scannable paragraphs and subheadings.",
    "persuasive": "Build a clear argument. Use evidence and rhetoric. End with a call to action.",
}

STRICTNESS_ADDONS: dict[str, str] = {
    "lenient": "Approve when the piece is clear, complete, and free of major issues. Perfection is not required.",
    "standard": "Be demanding. Only approve work that is genuinely publication-ready — polished, compelling, and complete. Good enough is not enough.",
    "harsh": "Hold the piece to the standard of a top-tier outlet like The Atlantic or The New Yorker. Approve only work that would survive a senior editor's desk.",
}


DEFAULT_LOG_DIR = Path(os.path.expanduser("~/.writing-loop/logs"))
CALL_TIMEOUT_SECONDS = 300
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]
SCORE_RE = re.compile(r"^\s*SCORE:\s*(\d+)\s*/\s*10\s*$")
APPROVED_WORD_RE = re.compile(r"\bAPPROVED\b")
NUMBERED_LIST_RE = re.compile(r"^\d+[.)]\s")


# ---------- small helpers ----------

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
    """Extract the score from 'SCORE: N/10', returning None if missing or out of [1,10]."""
    if not feedback:
        return None
    for line in feedback.strip().splitlines():
        m = SCORE_RE.match(line)
        if m:
            n = int(m.group(1))
            return n if 1 <= n <= 10 else None
    return None


def is_approved(feedback: str) -> bool:
    """True if the first content line (after any SCORE line) signals approval.

    Tolerant to common phrasings:
        APPROVED
        APPROVED — excellent work
        APPROVED: great voice
        Overall: APPROVED

    Rejects negations ('NOT APPROVED'), numbered feedback items, and substrings
    like 'APPROVEDISH'.
    """
    if not feedback:
        return False
    for line in feedback.strip().splitlines():
        stripped = line.strip()
        if not stripped or SCORE_RE.match(stripped):
            continue
        # Numbered feedback item = not approved
        if NUMBERED_LIST_RE.match(stripped):
            return False
        upper = stripped.upper()
        if "NOT APPROVED" in upper or "NOT_APPROVED" in upper:
            return False
        return APPROVED_WORD_RE.search(stripped) is not None
    return False


def format_trajectory(scores: list[int | None], approved: bool) -> str:
    """Render a score trajectory like '4/10 → 7/10 → 9/10 → 10/10 ✓'."""
    parts = [f"{s}/10" if s is not None else "?/10" for s in scores]
    arrow = " → ".join(parts) if parts else "(no scores)"
    if approved:
        arrow += " ✓"
    return arrow


def detect_plateau(scores: list[int | None], window: int) -> bool:
    """True if the last `window` scores are all present and identical."""
    if window < 2 or len(scores) < window:
        return False
    tail = scores[-window:]
    return all(s is not None and s == tail[0] for s in tail)


def format_output(draft: str, fmt: str) -> str:
    """Wrap the draft for the requested output format."""
    if fmt == "html":
        escaped = _html.escape(draft)
        paragraphs = [f"  <p>{p.strip()}</p>" for p in escaped.split("\n\n") if p.strip()]
        body = "\n".join(paragraphs)
        return (
            "<!DOCTYPE html>\n"
            "<html>\n"
            "<head><meta charset=\"utf-8\"></head>\n"
            "<body>\n"
            f"{body}\n"
            "</body>\n"
            "</html>\n"
        )
    return draft  # md and txt are the same for raw text


# ---------- system prompt composition ----------

def build_writer_system_prompt(style: str) -> str:
    addon = STYLE_ADDONS.get(style, "")
    if not addon:
        return BASE_WRITER_SYSTEM_PROMPT
    return f"{BASE_WRITER_SYSTEM_PROMPT}\n\nSTYLE GUIDANCE:\n- {addon}"


def build_editor_system_prompt(style: str, strictness: str) -> str:
    parts = [BASE_EDITOR_SYSTEM_PROMPT]
    strictness_addon = STRICTNESS_ADDONS.get(strictness, "")
    if strictness_addon:
        parts.append(f"APPROVAL STANDARD:\n- {strictness_addon}")
    style_addon = STYLE_ADDONS.get(style, "")
    if style_addon:
        parts.append(f"STYLE GUIDANCE (this piece is being written in this style):\n- {style_addon}")
    return "\n\n".join(parts)


# ---------- claude CLI wrapper ----------

def call_claude(
    user_prompt: str,
    system_prompt: str,
    model: str,
    retries: int = MAX_RETRIES,
    quiet: bool = False,
) -> str:
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
            if not quiet:
                print(f"\n  ⚠  Claude call failed (attempt {attempt + 1}/{retries + 1}): {last_error}")
                print(f"     Retrying in {delay}s...")
            time.sleep(delay)

    if isinstance(last_error, subprocess.TimeoutExpired):
        raise RuntimeError(f"Claude CLI timed out after {CALL_TIMEOUT_SECONDS}s") from last_error
    assert last_error is not None
    raise last_error


# ---------- output file writing ----------

def save_final(
    draft: str,
    output_path: str | None,
    log_dir: Path,
    output_format: str = "md",
) -> None:
    save_log(log_dir, "final_draft.md", draft)  # always keep raw in logs
    if output_path:
        formatted = format_output(draft, output_format)
        Path(output_path).expanduser().write_text(formatted, encoding="utf-8")


# ---------- prompt builders ----------

def build_writer_input(
    prompt: str,
    iteration: int,
    previous_draft: str | None,
    feedback_history: list[str],
) -> str:
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


def build_editor_input(
    prompt: str,
    draft: str,
    iteration: int,
    editor_history: list[str] | None = None,
    target_words: int | None = None,
) -> str:
    parts = [f"Please review the following piece:\n\n---\n{draft}\n---\n"]
    parts.append(f'\nOriginal prompt: "{prompt}"\n')
    parts.append(f"This is revision #{iteration}.\n")

    if editor_history:
        past = "\n\n".join(
            f"--- Your feedback on round {i + 1} ---\n{fb}"
            for i, fb in enumerate(editor_history)
        )
        parts.append(
            f"\nYour own prior feedback on earlier drafts (verify each point was addressed "
            f"and avoid repeating notes unnecessarily):\n\n{past}\n"
        )

    if target_words:
        current = len(draft.split())
        parts.append(
            f"\nTarget length: approximately {target_words} words. "
            f"Current draft: {current} words. "
            f"Factor length compliance into your evaluation.\n"
        )

    return "".join(parts)


# ---------- the main loop ----------

def run_loop(
    prompt: str,
    max_iterations: int,
    writer_model: str,
    editor_model: str,
    log_dir: Path,
    output_path: str | None,
    verbose: bool,
    input_draft: str | None = None,
    style: str = "default",
    strictness: str = "standard",
    target_words: int | None = None,
    approve_above: int | None = None,
    plateau_window: int = 3,
    output_format: str = "md",
    quiet: bool = False,
    json_output: bool = False,
) -> int:
    # In json mode, never emit human-readable chatter to stdout.
    quiet = quiet or json_output

    def say(msg: str = "", **kwargs) -> None:
        if not quiet:
            print(msg, **kwargs)

    run_id = generate_run_id()
    run_log_dir = create_log_dir(log_dir, run_id)

    writer_system = build_writer_system_prompt(style)
    editor_system = build_editor_system_prompt(style, strictness)

    say("Writing Loop v1.2")
    say(f'Prompt: "{prompt}"')
    if input_draft is not None:
        say(f"Starting from input draft ({len(input_draft.split())} words) — iteration 1 will skip the Writer.")
    say(f"Max iterations: {max_iterations} | Writer: {writer_model} | Editor: {editor_model}")
    say(f"Style: {style} | Strictness: {strictness}" + (f" | Target: ~{target_words} words" if target_words else ""))
    if approve_above is not None:
        say(f"Approval threshold: score ≥ {approve_above}/10 (or literal APPROVED)")
    say(f"Logs: {run_log_dir}")

    save_log(run_log_dir, "prompt.md", prompt)
    if input_draft is not None:
        save_log(run_log_dir, "input_draft.md", input_draft)

    feedback_history: list[str] = []
    scores: list[int | None] = []
    previous_draft: str | None = input_draft
    latest_draft: str | None = input_draft
    status = "max_iterations"
    iterations_run = 0

    try:
        for i in range(1, max_iterations + 1):
            iterations_run = i
            say()
            say("=" * 60)
            say(f"  Iteration {i}/{max_iterations}")
            say("=" * 60)

            # Writer phase — skip on iteration 1 if input draft was provided.
            if i == 1 and input_draft is not None:
                draft = input_draft
                say("  📄  Using provided input draft as iteration 1 starting point.")
            else:
                writer_input = build_writer_input(prompt, i, previous_draft, feedback_history)
                save_log(run_log_dir, f"iter{i:03d}_writer_input.md", writer_input)

                say(f"  ✍️  Writer is drafting (revision #{i})...", end="", flush=True)
                t0 = time.monotonic()
                draft = call_claude(writer_input, writer_system, writer_model, quiet=quiet)
                word_count = len(draft.split())
                say(f"  done ({time.monotonic() - t0:.1f}s, {word_count} words)")

                save_log(run_log_dir, f"iter{i:03d}_writer_output.md", draft)
                latest_draft = draft
                if verbose and not quiet:
                    print("\n--- Draft ---")
                    print(draft)
                    print("--- End Draft ---\n")

            # Editor phase
            editor_input = build_editor_input(
                prompt, draft, i,
                editor_history=feedback_history,
                target_words=target_words,
            )
            save_log(run_log_dir, f"iter{i:03d}_editor_input.md", editor_input)

            say(f"  🔍  Editor is reviewing draft #{i}...    ", end="", flush=True)
            t0 = time.monotonic()
            feedback = call_claude(editor_input, editor_system, editor_model, quiet=quiet)
            elapsed = time.monotonic() - t0

            score = parse_score(feedback)
            scores.append(score)
            score_str = f"{score}/10" if score is not None else "?/10"
            literal_approval = is_approved(feedback)
            threshold_approval = (
                approve_above is not None
                and score is not None
                and score >= approve_above
            )
            approved = literal_approval or threshold_approval
            status_str = "APPROVED ✓" if approved else "revisions requested"
            say(f"  done ({elapsed:.1f}s) — score {score_str} — {status_str}")

            save_log(run_log_dir, f"iter{i:03d}_editor_output.md", feedback)
            if verbose and not quiet:
                print("\n--- Feedback ---")
                print(feedback)
                print("--- End Feedback ---\n")

            if approved:
                status = "approved"
                save_log(run_log_dir, f"iter{i:03d}_APPROVED.md", f"# Approved\n\n{feedback}")
                save_final(draft, output_path, run_log_dir, output_format)
                latest_draft = draft
                say(f"\n  ✅ Approved after {i} iteration(s). Trajectory: {format_trajectory(scores, True)}")
                _finalize(
                    json_output, status, i, scores, run_log_dir, output_path,
                    latest_draft, output_format, say,
                )
                return 0

            feedback_history.append(feedback)
            previous_draft = draft
            latest_draft = draft

            if detect_plateau(scores, plateau_window):
                status = "plateau"
                say(f"\n  ⏸  Plateau detected: last {plateau_window} scores identical ({scores[-1]}/10). Stopping early.")
                save_final(latest_draft, output_path, run_log_dir, output_format)
                say(f"  Trajectory: {format_trajectory(scores, False)}")
                _finalize(
                    json_output, status, i, scores, run_log_dir, output_path,
                    latest_draft, output_format, say,
                )
                return 3

        say(f"\n  ⚠️  Max iterations ({max_iterations}) reached without approval.")
        say(f"  Trajectory: {format_trajectory(scores, False)}")
        save_final(latest_draft or "", output_path, run_log_dir, output_format)
        _finalize(
            json_output, status, iterations_run, scores, run_log_dir, output_path,
            latest_draft or "", output_format, say,
        )
        return 2

    except KeyboardInterrupt:
        status = "interrupted"
        say("\n\n  ⏹  Interrupted by user. Saving latest draft...")
        if latest_draft is not None:
            save_final(latest_draft, output_path, run_log_dir, output_format)
            _finalize(
                json_output, status, iterations_run, scores, run_log_dir, output_path,
                latest_draft, output_format, say,
            )
        else:
            say("  (No draft produced yet.)")
            _finalize(
                json_output, status, iterations_run, scores, run_log_dir, output_path,
                None, output_format, say,
            )
        return 130


def _finalize(
    json_output: bool,
    status: str,
    iterations: int,
    scores: list[int | None],
    run_log_dir: Path,
    output_path: str | None,
    final_draft: str | None,
    output_format: str,
    say,
) -> None:
    if json_output:
        payload = {
            "status": status,
            "iterations": iterations,
            "scores": scores,
            "log_dir": str(run_log_dir),
            "output_path": output_path,
            "output_format": output_format,
            "final_draft": final_draft,
        }
        print(json.dumps(payload, indent=2))
        return

    if output_path:
        say(f"\nFinal draft saved to: {output_path}")
    say(f"Full logs: {run_log_dir}")


# ---------- CLI ----------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="writing-loop",
        description="Run a Writer/Editor feedback loop to iteratively improve a piece of writing.",
    )
    p.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="The writing topic/instructions (pass '-' to read from stdin; optional with --prompt-file or --input-draft)",
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
    p.add_argument("--quiet", action="store_true", help="Suppress progress output")
    p.add_argument("--json", dest="json_output", action="store_true", help="Emit a JSON summary to stdout at the end (implies --quiet)")
    p.add_argument(
        "--style",
        choices=list(STYLE_ADDONS.keys()),
        default="default",
        help="Writing style preset that adjusts both Writer and Editor guidance",
    )
    p.add_argument(
        "--strictness",
        choices=list(STRICTNESS_ADDONS.keys()),
        default="standard",
        help="How harshly the Editor judges (default: standard)",
    )
    p.add_argument("--target-words", type=int, default=None, help="Target word count — the editor will also judge length compliance")
    p.add_argument(
        "--approve-above",
        type=int,
        default=None,
        metavar="N",
        help="Accept any draft scoring at or above N/10 in addition to literal APPROVED",
    )
    p.add_argument("--plateau-window", type=int, default=3, help="Stop early if the last N scores are identical (default: 3; set to 0 to disable)")
    p.add_argument("--format", dest="output_format", choices=["md", "txt", "html"], default="md", help="Output format for --output (default: md)")
    return p


def resolve_prompt(args: argparse.Namespace, stdin_reader=sys.stdin) -> str | None:
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
    if args.approve_above is not None and not (1 <= args.approve_above <= 10):
        parser.error("--approve-above must be between 1 and 10")
    if args.plateau_window < 0:
        parser.error("--plateau-window must be >= 0")

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
        style=args.style,
        strictness=args.strictness,
        target_words=args.target_words,
        approve_above=args.approve_above,
        plateau_window=args.plateau_window,
        output_format=args.output_format,
        quiet=args.quiet,
        json_output=args.json_output,
    )


if __name__ == "__main__":
    sys.exit(main())
