"""Tests for writing_loop.py (no network calls — subprocess is mocked)."""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import writing_loop  # noqa: E402


# --- Editor response fixtures used across tests ---
APPROVED_RESPONSE = "SCORE: 10/10\nAPPROVED\nStrong voice and tight structure."
FEEDBACK_RESPONSE_1 = "SCORE: 4/10\n1. Weak opening.\n2. Clichés in paragraph 2."
FEEDBACK_RESPONSE_2 = "SCORE: 7/10\n1. Tighten paragraph 3.\n2. Verb choices feel tired."


class GenerateRunIdTests(unittest.TestCase):
    def test_format_is_timestamp(self):
        run_id = writing_loop.generate_run_id()
        self.assertRegex(run_id, r"^\d{8}_\d{6}$")

    def test_ids_differ_across_calls(self):
        a = writing_loop.generate_run_id()
        with mock.patch("writing_loop.datetime") as dt:
            dt.now.return_value.strftime.return_value = "20260417_143023"
            b = writing_loop.generate_run_id()
        self.assertNotEqual(a, b)


class SaveLogTests(unittest.TestCase):
    def test_writes_file_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            path = writing_loop.save_log(log_dir, "foo.md", "hello world")
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), "hello world")

    def test_create_log_dir_makes_nested_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "a" / "b"
            run_dir = writing_loop.create_log_dir(base, "20260417_000000")
            self.assertTrue(run_dir.is_dir())
            self.assertEqual(run_dir.name, "20260417_000000")


class ParseScoreTests(unittest.TestCase):
    def test_parses_clean_score(self):
        self.assertEqual(writing_loop.parse_score("SCORE: 7/10\n1. foo"), 7)

    def test_parses_with_extra_whitespace(self):
        self.assertEqual(writing_loop.parse_score("  SCORE:  9 / 10  \nstuff"), 9)

    def test_returns_none_when_missing(self):
        self.assertIsNone(writing_loop.parse_score("no score here\n1. foo"))

    def test_returns_none_on_empty(self):
        self.assertIsNone(writing_loop.parse_score(""))

    def test_handles_score_10(self):
        self.assertEqual(writing_loop.parse_score("SCORE: 10/10\nAPPROVED"), 10)

    def test_handles_score_1(self):
        self.assertEqual(writing_loop.parse_score("SCORE: 1/10\nhopeless"), 1)

    def test_finds_score_on_later_line(self):
        self.assertEqual(writing_loop.parse_score("\n\nSCORE: 5/10\n"), 5)


class IsApprovedTests(unittest.TestCase):
    def test_approved_with_score_prefix(self):
        self.assertTrue(writing_loop.is_approved("SCORE: 10/10\nAPPROVED\nGreat."))

    def test_approved_without_score_still_works(self):
        # Backwards compatibility — if model forgets SCORE line.
        self.assertTrue(writing_loop.is_approved("APPROVED\nGreat."))

    def test_approved_with_leading_blank_lines(self):
        self.assertTrue(writing_loop.is_approved("\n\nSCORE: 9/10\n\nAPPROVED\nnice."))

    def test_not_approved_when_feedback_list(self):
        self.assertFalse(writing_loop.is_approved("SCORE: 6/10\n1. Weak opening.\n2. More please."))

    def test_not_approved_when_substring(self):
        self.assertFalse(writing_loop.is_approved("APPROVEDISH"))

    def test_not_approved_when_in_middle(self):
        self.assertFalse(writing_loop.is_approved("1. Tighten opening.\nAPPROVED"))

    def test_empty_string(self):
        self.assertFalse(writing_loop.is_approved(""))

    def test_score_only_no_verdict(self):
        self.assertFalse(writing_loop.is_approved("SCORE: 5/10"))


class BuildWriterInputTests(unittest.TestCase):
    def test_first_iteration_uses_prompt_only(self):
        out = writing_loop.build_writer_input("Topic X", 1, None, [])
        self.assertIn("Topic X", out)
        self.assertNotIn("previous draft", out)

    def test_second_iteration_includes_draft_and_latest_feedback(self):
        out = writing_loop.build_writer_input("Topic X", 2, "DRAFT_1", ["FB_1"])
        self.assertIn("DRAFT_1", out)
        self.assertIn("FB_1", out)
        self.assertIn("previous draft", out)
        # With only one item in history there is no "prior feedback already addressed" block.
        self.assertNotIn("already addressed", out)

    def test_third_iteration_includes_prior_feedback_history(self):
        out = writing_loop.build_writer_input("Topic X", 3, "DRAFT_2", ["FB_1", "FB_2"])
        self.assertIn("DRAFT_2", out)
        self.assertIn("FB_1", out)  # prior feedback preserved
        self.assertIn("FB_2", out)  # latest feedback
        self.assertIn("already addressed", out)
        self.assertIn("LATEST feedback", out)

    def test_fourth_iteration_accumulates_all_prior(self):
        out = writing_loop.build_writer_input(
            "Topic X", 4, "DRAFT_3", ["FB_A", "FB_B", "FB_C"],
        )
        for expected in ("FB_A", "FB_B", "FB_C", "DRAFT_3"):
            self.assertIn(expected, out)

    def test_empty_feedback_history_falls_back_to_prompt_only(self):
        # Defensive: even if iteration > 1, an empty history means first-pass behavior.
        out = writing_loop.build_writer_input("Topic X", 2, None, [])
        self.assertIn("Topic X", out)
        self.assertNotIn("previous draft", out)


class BuildEditorInputTests(unittest.TestCase):
    def test_includes_prompt_and_draft(self):
        out = writing_loop.build_editor_input("Topic Y", "MY DRAFT", 3)
        self.assertIn("MY DRAFT", out)
        self.assertIn("Topic Y", out)
        self.assertIn("revision #3", out)


class ArgParserTests(unittest.TestCase):
    def test_defaults(self):
        parser = writing_loop.build_arg_parser()
        args = parser.parse_args(["my prompt"])
        self.assertEqual(args.prompt, "my prompt")
        self.assertEqual(args.max_iterations, 5)
        self.assertEqual(args.writer_model, "sonnet")
        self.assertEqual(args.editor_model, "sonnet")
        self.assertIsNone(args.output)
        self.assertIsNone(args.prompt_file)
        self.assertIsNone(args.input_draft)
        self.assertFalse(args.verbose)

    def test_overrides(self):
        parser = writing_loop.build_arg_parser()
        args = parser.parse_args([
            "topic",
            "--max-iterations", "3",
            "--writer-model", "opus",
            "--editor-model", "haiku",
            "--output", "out.md",
            "--log-dir", "/tmp/logs",
            "--verbose",
            "--prompt-file", "/tmp/p.md",
            "--input-draft", "/tmp/d.md",
        ])
        self.assertEqual(args.max_iterations, 3)
        self.assertEqual(args.writer_model, "opus")
        self.assertEqual(args.editor_model, "haiku")
        self.assertEqual(args.output, "out.md")
        self.assertEqual(args.log_dir, "/tmp/logs")
        self.assertTrue(args.verbose)
        self.assertEqual(args.prompt_file, "/tmp/p.md")
        self.assertEqual(args.input_draft, "/tmp/d.md")

    def test_prompt_is_optional(self):
        parser = writing_loop.build_arg_parser()
        args = parser.parse_args(["--prompt-file", "/tmp/x"])
        self.assertIsNone(args.prompt)


class ResolvePromptTests(unittest.TestCase):
    def test_reads_from_prompt_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("  prompt from file\n")
            path = f.name
        try:
            parser = writing_loop.build_arg_parser()
            args = parser.parse_args(["--prompt-file", path])
            self.assertEqual(writing_loop.resolve_prompt(args), "prompt from file")
        finally:
            Path(path).unlink()

    def test_reads_from_stdin_when_dash(self):
        parser = writing_loop.build_arg_parser()
        args = parser.parse_args(["-"])
        fake_stdin = io.StringIO("stdin prompt text\n")
        self.assertEqual(writing_loop.resolve_prompt(args, stdin_reader=fake_stdin), "stdin prompt text")

    def test_returns_positional_when_given(self):
        parser = writing_loop.build_arg_parser()
        args = parser.parse_args(["inline topic"])
        self.assertEqual(writing_loop.resolve_prompt(args), "inline topic")

    def test_returns_none_when_nothing_given(self):
        parser = writing_loop.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(writing_loop.resolve_prompt(args))


class CallClaudeTests(unittest.TestCase):
    def test_builds_correct_command_and_returns_stdout(self):
        fake_result = mock.Mock(returncode=0, stdout="the draft\n", stderr="")
        with mock.patch("writing_loop.subprocess.run", return_value=fake_result) as run:
            out = writing_loop.call_claude("USER", "SYS", "sonnet")
        self.assertEqual(out, "the draft")
        args, kwargs = run.call_args
        cmd = args[0]
        self.assertEqual(cmd[0], "claude")
        self.assertIn("--print", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("sonnet", cmd)
        self.assertIn("--system-prompt", cmd)
        self.assertIn("SYS", cmd)
        self.assertEqual(kwargs["input"], "USER")

    def test_retries_on_nonzero_exit_then_succeeds(self):
        results = [
            mock.Mock(returncode=1, stdout="", stderr="flaky"),
            mock.Mock(returncode=0, stdout="eventually ok", stderr=""),
        ]
        with mock.patch("writing_loop.subprocess.run", side_effect=results) as run, \
             mock.patch("writing_loop.time.sleep") as sleep, \
             mock.patch("sys.stdout", io.StringIO()):
            out = writing_loop.call_claude("x", "y", "sonnet", retries=2)
        self.assertEqual(out, "eventually ok")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        # First retry uses RETRY_DELAYS[0]
        self.assertEqual(sleep.call_args.args[0], writing_loop.RETRY_DELAYS[0])

    def test_retries_exhausted_raises_last_error(self):
        fake = mock.Mock(returncode=1, stdout="", stderr="persistent boom")
        with mock.patch("writing_loop.subprocess.run", return_value=fake) as run, \
             mock.patch("writing_loop.time.sleep"), \
             mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(RuntimeError) as ctx:
                writing_loop.call_claude("x", "y", "sonnet", retries=2)
        self.assertIn("persistent boom", str(ctx.exception))
        self.assertEqual(run.call_count, 3)  # 1 initial + 2 retries

    def test_retries_on_timeout(self):
        results = [
            subprocess.TimeoutExpired(cmd="claude", timeout=1),
            mock.Mock(returncode=0, stdout="ok after timeout", stderr=""),
        ]
        with mock.patch("writing_loop.subprocess.run", side_effect=results), \
             mock.patch("writing_loop.time.sleep"), \
             mock.patch("sys.stdout", io.StringIO()):
            out = writing_loop.call_claude("x", "y", "sonnet", retries=2)
        self.assertEqual(out, "ok after timeout")

    def test_timeout_exhausted_raises_runtime_error(self):
        with mock.patch(
            "writing_loop.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=1),
        ), mock.patch("writing_loop.time.sleep"), mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(RuntimeError) as ctx:
                writing_loop.call_claude("x", "y", "sonnet", retries=1)
        self.assertIn("timed out", str(ctx.exception).lower())

    def test_no_retry_when_claude_not_found(self):
        with mock.patch("writing_loop.subprocess.run", side_effect=FileNotFoundError()) as run, \
             mock.patch("writing_loop.time.sleep") as sleep:
            with self.assertRaises(RuntimeError) as ctx:
                writing_loop.call_claude("x", "y", "sonnet")
        self.assertIn("claude", str(ctx.exception).lower())
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()

    def test_zero_retries_means_single_attempt(self):
        fake = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch("writing_loop.subprocess.run", return_value=fake) as run, \
             mock.patch("writing_loop.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                writing_loop.call_claude("x", "y", "sonnet", retries=0)
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()


class SaveFinalTests(unittest.TestCase):
    def test_writes_to_log_dir_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            writing_loop.save_final("the draft", None, log_dir)
            self.assertEqual((log_dir / "final_draft.md").read_text(), "the draft")

    def test_writes_to_output_path_when_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()
            out = Path(tmp) / "out.md"
            writing_loop.save_final("the draft", str(out), log_dir)
            self.assertEqual(out.read_text(), "the draft")
            self.assertEqual((log_dir / "final_draft.md").read_text(), "the draft")


class RunLoopIntegrationTests(unittest.TestCase):
    """End-to-end loop with call_claude mocked."""

    def _run(self, tmp, outputs_list, **overrides):
        outputs = iter(outputs_list)
        kwargs = dict(
            prompt="do the thing",
            max_iterations=5,
            writer_model="sonnet",
            editor_model="sonnet",
            log_dir=Path(tmp),
            output_path=None,
            verbose=False,
        )
        kwargs.update(overrides)
        with mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=lambda *a, **k: next(outputs)):
            return writing_loop.run_loop(**kwargs)

    def test_stops_when_editor_approves(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run(
                tmp,
                ["DRAFT ONE", FEEDBACK_RESPONSE_1, "DRAFT TWO", APPROVED_RESPONSE],
            )
            self.assertEqual(code, 0)
            run_dir = next(Path(tmp).iterdir())
            files = {p.name for p in run_dir.iterdir()}
            self.assertIn("iter002_APPROVED.md", files)
            self.assertEqual((run_dir / "final_draft.md").read_text(), "DRAFT TWO")

    def test_stops_after_max_iterations(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run(
                tmp,
                ["never-approved"] * 10,
                prompt="x",
                max_iterations=2,
            )
            self.assertEqual(code, 2)
            run_dir = next(Path(tmp).iterdir())
            files = {p.name for p in run_dir.iterdir()}
            self.assertIn("final_draft.md", files)
            self.assertIn("iter001_writer_output.md", files)
            self.assertIn("iter002_editor_output.md", files)

    def test_writer_receives_accumulated_feedback_history(self):
        """Verify the Writer's input in iteration 3 includes feedback from iterations 1 AND 2."""
        captured_writer_inputs = []
        script = iter([
            "DRAFT_1",            # writer iter 1
            FEEDBACK_RESPONSE_1,  # editor iter 1 (contains "Weak opening")
            "DRAFT_2",            # writer iter 2
            FEEDBACK_RESPONSE_2,  # editor iter 2 (contains "Tighten paragraph 3")
            "DRAFT_3",            # writer iter 3  <-- must see both prior feedbacks
            APPROVED_RESPONSE,    # editor iter 3
        ])

        def fake_call(user_prompt, system_prompt, model):
            if system_prompt == writing_loop.WRITER_SYSTEM_PROMPT:
                captured_writer_inputs.append(user_prompt)
            return next(script)

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=fake_call):
            writing_loop.run_loop(
                prompt="topic",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
            )

        self.assertEqual(len(captured_writer_inputs), 3)
        iter3_input = captured_writer_inputs[2]
        self.assertIn("Weak opening", iter3_input)           # from round 1
        self.assertIn("Tighten paragraph 3", iter3_input)    # from round 2 (latest)
        self.assertIn("already addressed", iter3_input)
        self.assertIn("LATEST feedback", iter3_input)
        self.assertIn("DRAFT_2", iter3_input)                # previous draft

    def test_input_draft_skips_writer_on_iteration_1(self):
        """With input_draft, iter 1 sends draft straight to editor; writer runs from iter 2."""
        writer_calls = []
        editor_calls = []

        def fake_call(user_prompt, system_prompt, model):
            if system_prompt == writing_loop.WRITER_SYSTEM_PROMPT:
                writer_calls.append(user_prompt)
                return f"WRITER_OUTPUT_{len(writer_calls)}"
            editor_calls.append(user_prompt)
            # Approve on 2nd editor call so loop ends after iter 2.
            return APPROVED_RESPONSE if len(editor_calls) >= 2 else FEEDBACK_RESPONSE_1

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=fake_call):
            code = writing_loop.run_loop(
                prompt="brief context",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
                input_draft="MY_STARTING_DRAFT",
            )
            self.assertEqual(code, 0)
            # Writer only called once (for iter 2), not for iter 1.
            self.assertEqual(len(writer_calls), 1)
            self.assertIn("MY_STARTING_DRAFT", editor_calls[0])
            self.assertIn("MY_STARTING_DRAFT", writer_calls[0])
            run_dir = next(Path(tmp).iterdir())
            files = {p.name for p in run_dir.iterdir()}
            self.assertIn("input_draft.md", files)
            self.assertNotIn("iter001_writer_input.md", files)

    def test_input_draft_approved_immediately(self):
        """If the editor approves the input draft on iter 1, writer never runs at all."""
        writer_calls = []

        def fake_call(user_prompt, system_prompt, model):
            if system_prompt == writing_loop.WRITER_SYSTEM_PROMPT:
                writer_calls.append(user_prompt)
                return "UNUSED"
            return APPROVED_RESPONSE

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=fake_call):
            code = writing_loop.run_loop(
                prompt="polish this",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
                input_draft="PERFECT_DRAFT",
            )
            self.assertEqual(code, 0)
            self.assertEqual(writer_calls, [])
            run_dir = next(Path(tmp).iterdir())
            self.assertEqual((run_dir / "final_draft.md").read_text(), "PERFECT_DRAFT")

    def test_verbose_prints_drafts_and_feedback(self):
        buf = io.StringIO()
        outputs = iter(["D1", FEEDBACK_RESPONSE_1, "D2", APPROVED_RESPONSE])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", buf), \
             mock.patch("writing_loop.call_claude", side_effect=lambda *a, **k: next(outputs)):
            writing_loop.run_loop(
                prompt="x",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=True,
            )
        out = buf.getvalue()
        self.assertIn("D1", out)
        self.assertIn("Weak opening", out)
        self.assertIn("--- Draft ---", out)
        self.assertIn("--- Feedback ---", out)

    def test_score_appears_in_progress_output(self):
        buf = io.StringIO()
        outputs = iter(["D1", "SCORE: 8/10\n1. ok\n2. stuff", "D2", APPROVED_RESPONSE])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", buf), \
             mock.patch("writing_loop.call_claude", side_effect=lambda *a, **k: next(outputs)):
            writing_loop.run_loop(
                prompt="x",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
            )
        out = buf.getvalue()
        self.assertIn("8/10", out)
        self.assertIn("10/10", out)
        self.assertIn("revisions requested", out)
        self.assertIn("APPROVED", out)

    def test_score_missing_falls_back_to_question_mark(self):
        buf = io.StringIO()
        outputs = iter(["D1", APPROVED_RESPONSE.replace("SCORE: 10/10\n", "")])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", buf), \
             mock.patch("writing_loop.call_claude", side_effect=lambda *a, **k: next(outputs)):
            code = writing_loop.run_loop(
                prompt="x",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
            )
        self.assertEqual(code, 0)  # still approves (backwards-compatible)
        self.assertIn("?/10", buf.getvalue())

    def test_output_path_receives_final_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "essay.md"
            code = self._run(
                tmp,
                ["FINAL DRAFT", APPROVED_RESPONSE],
                output_path=str(out_path),
            )
            self.assertEqual(code, 0)
            self.assertEqual(out_path.read_text(), "FINAL DRAFT")

    def test_keyboard_interrupt_saves_latest_draft(self):
        calls = iter([
            lambda: "DRAFT_BEFORE_INTERRUPT",
            lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
        ])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=lambda *a, **k: next(calls)()):
            code = writing_loop.run_loop(
                prompt="x",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
            )
            self.assertEqual(code, 130)
            run_dir = next(Path(tmp).iterdir())
            self.assertEqual((run_dir / "final_draft.md").read_text(), "DRAFT_BEFORE_INTERRUPT")

    def test_keyboard_interrupt_before_any_draft(self):
        def boom(*a, **k):
            raise KeyboardInterrupt()

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=boom):
            code = writing_loop.run_loop(
                prompt="x",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
            )
            self.assertEqual(code, 130)
            run_dir = next(Path(tmp).iterdir())
            self.assertNotIn("final_draft.md", {p.name for p in run_dir.iterdir()})

    def test_keyboard_interrupt_with_input_draft_saves_input(self):
        """If interrupted before the writer runs, the input draft is still saved as latest."""
        def boom(*a, **k):
            raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=boom):
            code = writing_loop.run_loop(
                prompt="x",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
                input_draft="STARTING_DRAFT",
            )
            self.assertEqual(code, 130)
            run_dir = next(Path(tmp).iterdir())
            self.assertEqual((run_dir / "final_draft.md").read_text(), "STARTING_DRAFT")


class MainEntrypointTests(unittest.TestCase):
    def test_main_passes_args_through_to_run_loop(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("writing_loop.run_loop", return_value=0) as rl:
            code = writing_loop.main([
                "a topic",
                "--max-iterations", "3",
                "--writer-model", "opus",
                "--editor-model", "haiku",
                "--log-dir", tmp,
                "--output", str(Path(tmp) / "out.md"),
                "--verbose",
            ])
        self.assertEqual(code, 0)
        kwargs = rl.call_args.kwargs
        self.assertEqual(kwargs["prompt"], "a topic")
        self.assertEqual(kwargs["max_iterations"], 3)
        self.assertEqual(kwargs["writer_model"], "opus")
        self.assertEqual(kwargs["editor_model"], "haiku")
        self.assertTrue(kwargs["verbose"])
        self.assertEqual(kwargs["log_dir"], Path(tmp))
        self.assertIsNone(kwargs["input_draft"])

    def test_main_reads_prompt_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("file-based prompt\n")
            ppath = f.name
        try:
            with mock.patch("writing_loop.run_loop", return_value=0) as rl:
                writing_loop.main(["--prompt-file", ppath])
            self.assertEqual(rl.call_args.kwargs["prompt"], "file-based prompt")
        finally:
            Path(ppath).unlink()

    def test_main_reads_input_draft(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("my existing draft\n")
            dpath = f.name
        try:
            with mock.patch("writing_loop.run_loop", return_value=0) as rl:
                writing_loop.main(["a topic", "--input-draft", dpath])
            self.assertEqual(rl.call_args.kwargs["input_draft"], "my existing draft")
        finally:
            Path(dpath).unlink()

    def test_main_input_draft_without_prompt_uses_fallback(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("orphan draft")
            dpath = f.name
        try:
            with mock.patch("writing_loop.run_loop", return_value=0) as rl:
                writing_loop.main(["--input-draft", dpath])
            self.assertIsNotNone(rl.call_args.kwargs["prompt"])
            self.assertIn("Polish", rl.call_args.kwargs["prompt"])
        finally:
            Path(dpath).unlink()

    def test_main_errors_without_prompt_or_draft(self):
        with self.assertRaises(SystemExit):
            writing_loop.main([])

    def test_main_rejects_zero_iterations(self):
        with self.assertRaises(SystemExit):
            writing_loop.main(["topic", "--max-iterations", "0"])

    def test_main_rejects_negative_iterations(self):
        with self.assertRaises(SystemExit):
            writing_loop.main(["topic", "--max-iterations", "-1"])

    def test_main_expands_log_dir_tilde(self):
        with mock.patch("writing_loop.run_loop", return_value=0) as rl:
            writing_loop.main(["topic", "--log-dir", "~/myloops"])
        log_dir = rl.call_args.kwargs["log_dir"]
        self.assertFalse(str(log_dir).startswith("~"))


class PromptConstantsTests(unittest.TestCase):
    def test_writer_prompt_is_nonempty(self):
        self.assertIn("writer", writing_loop.WRITER_SYSTEM_PROMPT.lower())

    def test_editor_prompt_requires_approved_token(self):
        self.assertIn("APPROVED", writing_loop.EDITOR_SYSTEM_PROMPT)

    def test_editor_prompt_requires_score(self):
        self.assertIn("SCORE:", writing_loop.EDITOR_SYSTEM_PROMPT)

    def test_writer_prompt_mentions_not_regressing(self):
        self.assertIn("regress", writing_loop.WRITER_SYSTEM_PROMPT.lower())


if __name__ == "__main__":
    unittest.main()
