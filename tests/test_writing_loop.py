"""Tests for writing_loop.py (no network calls — subprocess is mocked)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import writing_loop  # noqa: E402


# --- Editor response fixtures ---
APPROVED_RESPONSE = "SCORE: 10/10\nAPPROVED\nStrong voice and tight structure."
FEEDBACK_RESPONSE_1 = "SCORE: 4/10\n1. Weak opening.\n2. Clichés in paragraph 2."
FEEDBACK_RESPONSE_2 = "SCORE: 7/10\n1. Tighten paragraph 3.\n2. Verb choices feel tired."


# ---------------- simple helpers ----------------

class GenerateRunIdTests(unittest.TestCase):
    def test_format_is_timestamp(self):
        self.assertRegex(writing_loop.generate_run_id(), r"^\d{8}_\d{6}$")

    def test_ids_differ_across_calls(self):
        a = writing_loop.generate_run_id()
        with mock.patch("writing_loop.datetime") as dt:
            dt.now.return_value.strftime.return_value = "20260417_143023"
            b = writing_loop.generate_run_id()
        self.assertNotEqual(a, b)


class SaveLogTests(unittest.TestCase):
    def test_writes_file_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = writing_loop.save_log(Path(tmp), "foo.md", "hello")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello")

    def test_create_log_dir_makes_nested_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = writing_loop.create_log_dir(Path(tmp) / "a" / "b", "20260417_000000")
            self.assertTrue(run_dir.is_dir())
            self.assertEqual(run_dir.name, "20260417_000000")


# ---------------- parse_score ----------------

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

    def test_rejects_score_0(self):
        self.assertIsNone(writing_loop.parse_score("SCORE: 0/10"))

    def test_rejects_score_above_10(self):
        self.assertIsNone(writing_loop.parse_score("SCORE: 11/10"))
        self.assertIsNone(writing_loop.parse_score("SCORE: 99/10"))

    def test_finds_score_on_later_line(self):
        self.assertEqual(writing_loop.parse_score("\n\nSCORE: 5/10\n"), 5)


# ---------------- is_approved (tolerant) ----------------

class IsApprovedTests(unittest.TestCase):
    def test_approved_with_score_prefix(self):
        self.assertTrue(writing_loop.is_approved("SCORE: 10/10\nAPPROVED\nGreat."))

    def test_approved_without_score_still_works(self):
        self.assertTrue(writing_loop.is_approved("APPROVED\nGreat."))

    def test_approved_with_em_dash_suffix(self):
        self.assertTrue(writing_loop.is_approved("SCORE: 10/10\nAPPROVED — excellent work"))

    def test_approved_with_colon_suffix(self):
        self.assertTrue(writing_loop.is_approved("SCORE: 10/10\nAPPROVED: strong piece"))

    def test_approved_with_label_prefix(self):
        self.assertTrue(writing_loop.is_approved("SCORE: 10/10\nOverall: APPROVED"))

    def test_approved_with_leading_blank_lines(self):
        self.assertTrue(writing_loop.is_approved("\n\nSCORE: 9/10\n\nAPPROVED\nnice."))

    def test_rejects_not_approved(self):
        self.assertFalse(writing_loop.is_approved("SCORE: 4/10\nNOT APPROVED — needs rewrite"))

    def test_rejects_feedback_list(self):
        self.assertFalse(writing_loop.is_approved("SCORE: 6/10\n1. Weak opening.\n2. More."))

    def test_rejects_numbered_list_with_paren(self):
        self.assertFalse(writing_loop.is_approved("SCORE: 6/10\n1) Weak opening."))

    def test_rejects_substring_approvedish(self):
        self.assertFalse(writing_loop.is_approved("APPROVEDISH"))

    def test_rejects_when_in_middle_of_feedback(self):
        self.assertFalse(writing_loop.is_approved("1. Tighten opening.\nAPPROVED"))

    def test_empty_string(self):
        self.assertFalse(writing_loop.is_approved(""))

    def test_score_only_no_verdict(self):
        self.assertFalse(writing_loop.is_approved("SCORE: 5/10"))


# ---------------- trajectory + plateau ----------------

class FormatTrajectoryTests(unittest.TestCase):
    def test_single_score(self):
        self.assertEqual(writing_loop.format_trajectory([7], False), "7/10")

    def test_with_approval_marker(self):
        self.assertEqual(
            writing_loop.format_trajectory([4, 7, 10], True),
            "4/10 → 7/10 → 10/10 ✓",
        )

    def test_missing_scores_render_as_question_mark(self):
        self.assertEqual(
            writing_loop.format_trajectory([None, 5], False),
            "?/10 → 5/10",
        )

    def test_empty(self):
        self.assertEqual(writing_loop.format_trajectory([], False), "(no scores)")


class DetectPlateauTests(unittest.TestCase):
    def test_detects_flat_tail(self):
        self.assertTrue(writing_loop.detect_plateau([4, 7, 7, 7], window=3))

    def test_no_plateau_when_varied(self):
        self.assertFalse(writing_loop.detect_plateau([7, 7, 8], window=3))

    def test_too_few_scores(self):
        self.assertFalse(writing_loop.detect_plateau([7, 7], window=3))

    def test_window_zero_disables(self):
        self.assertFalse(writing_loop.detect_plateau([7, 7, 7], window=0))

    def test_none_in_tail_means_no_plateau(self):
        self.assertFalse(writing_loop.detect_plateau([7, None, 7], window=3))


# ---------------- format_output ----------------

class FormatOutputTests(unittest.TestCase):
    def test_md_is_passthrough(self):
        self.assertEqual(writing_loop.format_output("hello world", "md"), "hello world")

    def test_txt_is_passthrough(self):
        self.assertEqual(writing_loop.format_output("hello world", "txt"), "hello world")

    def test_html_wraps_and_escapes(self):
        out = writing_loop.format_output("first para\n\nsecond <script>", "html")
        self.assertIn("<!DOCTYPE html>", out)
        self.assertIn("<p>first para</p>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>", out)  # escaped, not raw


# ---------------- system prompt composition ----------------

class SystemPromptTests(unittest.TestCase):
    def test_writer_default_has_no_style_guidance(self):
        out = writing_loop.build_writer_system_prompt("default")
        self.assertEqual(out, writing_loop.BASE_WRITER_SYSTEM_PROMPT)

    def test_writer_with_style_appends_guidance(self):
        out = writing_loop.build_writer_system_prompt("academic")
        self.assertIn("STYLE GUIDANCE", out)
        self.assertIn("formal academic tone", out)

    def test_editor_with_strictness_and_style(self):
        out = writing_loop.build_editor_system_prompt("journalistic", "harsh")
        self.assertIn("APPROVAL STANDARD", out)
        self.assertIn("Atlantic", out)
        self.assertIn("STYLE GUIDANCE", out)
        self.assertIn("AP style", out)

    def test_editor_standard_strictness_adds_standard_note(self):
        out = writing_loop.build_editor_system_prompt("default", "standard")
        self.assertIn("APPROVAL STANDARD", out)
        self.assertIn("publication-ready", out)


# ---------------- writer input builder ----------------

class BuildWriterInputTests(unittest.TestCase):
    def test_first_iteration_uses_prompt_only(self):
        out = writing_loop.build_writer_input("Topic X", 1, None, [])
        self.assertIn("Topic X", out)
        self.assertNotIn("previous draft", out)

    def test_second_iteration_includes_draft_and_latest_feedback(self):
        out = writing_loop.build_writer_input("Topic X", 2, "DRAFT_1", ["FB_1"])
        self.assertIn("DRAFT_1", out)
        self.assertIn("FB_1", out)
        self.assertNotIn("already addressed", out)

    def test_third_iteration_includes_prior_feedback_history(self):
        out = writing_loop.build_writer_input("Topic X", 3, "DRAFT_2", ["FB_1", "FB_2"])
        self.assertIn("FB_1", out)
        self.assertIn("FB_2", out)
        self.assertIn("already addressed", out)
        self.assertIn("LATEST feedback", out)

    def test_empty_feedback_history_falls_back_to_prompt_only(self):
        out = writing_loop.build_writer_input("Topic X", 2, None, [])
        self.assertIn("Topic X", out)
        self.assertNotIn("previous draft", out)


# ---------------- editor input builder ----------------

class BuildEditorInputTests(unittest.TestCase):
    def test_includes_prompt_and_draft(self):
        out = writing_loop.build_editor_input("Topic Y", "MY DRAFT", 3)
        self.assertIn("MY DRAFT", out)
        self.assertIn("Topic Y", out)
        self.assertIn("revision #3", out)

    def test_includes_editor_history_when_provided(self):
        out = writing_loop.build_editor_input(
            "Topic Y", "MY DRAFT", 3,
            editor_history=["prior feedback A", "prior feedback B"],
        )
        self.assertIn("prior feedback A", out)
        self.assertIn("prior feedback B", out)
        self.assertIn("verify each point was addressed", out)

    def test_includes_target_words_note(self):
        out = writing_loop.build_editor_input(
            "Topic Y", "one two three four five", 1, target_words=500,
        )
        self.assertIn("Target length", out)
        self.assertIn("500 words", out)
        self.assertIn("5 words", out)  # current draft


# ---------------- CLI / arg parser ----------------

class ArgParserTests(unittest.TestCase):
    def test_defaults(self):
        args = writing_loop.build_arg_parser().parse_args(["my prompt"])
        self.assertEqual(args.prompt, "my prompt")
        self.assertEqual(args.max_iterations, 5)
        self.assertEqual(args.writer_model, "sonnet")
        self.assertEqual(args.editor_model, "sonnet")
        self.assertIsNone(args.output)
        self.assertIsNone(args.prompt_file)
        self.assertIsNone(args.input_draft)
        self.assertFalse(args.verbose)
        self.assertFalse(args.quiet)
        self.assertFalse(args.json_output)
        self.assertEqual(args.style, "default")
        self.assertEqual(args.strictness, "standard")
        self.assertIsNone(args.target_words)
        self.assertIsNone(args.approve_above)
        self.assertEqual(args.plateau_window, 3)
        self.assertEqual(args.output_format, "md")

    def test_all_overrides(self):
        args = writing_loop.build_arg_parser().parse_args([
            "topic",
            "--max-iterations", "3",
            "--writer-model", "opus",
            "--editor-model", "haiku",
            "--output", "out.md",
            "--log-dir", "/tmp/logs",
            "--verbose",
            "--quiet",
            "--json",
            "--style", "fiction",
            "--strictness", "harsh",
            "--target-words", "500",
            "--approve-above", "8",
            "--plateau-window", "2",
            "--format", "html",
        ])
        self.assertEqual(args.max_iterations, 3)
        self.assertTrue(args.quiet)
        self.assertTrue(args.json_output)
        self.assertEqual(args.style, "fiction")
        self.assertEqual(args.strictness, "harsh")
        self.assertEqual(args.target_words, 500)
        self.assertEqual(args.approve_above, 8)
        self.assertEqual(args.plateau_window, 2)
        self.assertEqual(args.output_format, "html")

    def test_prompt_is_optional(self):
        args = writing_loop.build_arg_parser().parse_args(["--prompt-file", "/tmp/x"])
        self.assertIsNone(args.prompt)


class ResolvePromptTests(unittest.TestCase):
    def test_reads_from_prompt_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("  from file\n")
            path = f.name
        try:
            args = writing_loop.build_arg_parser().parse_args(["--prompt-file", path])
            self.assertEqual(writing_loop.resolve_prompt(args), "from file")
        finally:
            Path(path).unlink()

    def test_reads_from_stdin_when_dash(self):
        args = writing_loop.build_arg_parser().parse_args(["-"])
        fake = io.StringIO("stdin text\n")
        self.assertEqual(writing_loop.resolve_prompt(args, stdin_reader=fake), "stdin text")

    def test_returns_positional_when_given(self):
        args = writing_loop.build_arg_parser().parse_args(["inline topic"])
        self.assertEqual(writing_loop.resolve_prompt(args), "inline topic")

    def test_returns_none_when_nothing_given(self):
        args = writing_loop.build_arg_parser().parse_args([])
        self.assertIsNone(writing_loop.resolve_prompt(args))


# ---------------- call_claude (retries) ----------------

class CallClaudeTests(unittest.TestCase):
    def test_builds_correct_command_and_returns_stdout(self):
        fake = mock.Mock(returncode=0, stdout="the draft\n", stderr="")
        with mock.patch("writing_loop.subprocess.run", return_value=fake) as run:
            out = writing_loop.call_claude("USER", "SYS", "sonnet")
        self.assertEqual(out, "the draft")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "claude")
        self.assertIn("--print", cmd)
        self.assertIn("sonnet", cmd)
        self.assertIn("SYS", cmd)
        self.assertEqual(run.call_args.kwargs["input"], "USER")

    def test_retries_on_nonzero_exit_then_succeeds(self):
        results = [
            mock.Mock(returncode=1, stdout="", stderr="flaky"),
            mock.Mock(returncode=0, stdout="ok", stderr=""),
        ]
        with mock.patch("writing_loop.subprocess.run", side_effect=results) as run, \
             mock.patch("writing_loop.time.sleep") as sleep, \
             mock.patch("sys.stdout", io.StringIO()):
            out = writing_loop.call_claude("x", "y", "sonnet", retries=2)
        self.assertEqual(out, "ok")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(sleep.call_args.args[0], writing_loop.RETRY_DELAYS[0])

    def test_retries_exhausted_raises_last_error(self):
        fake = mock.Mock(returncode=1, stdout="", stderr="persistent boom")
        with mock.patch("writing_loop.subprocess.run", return_value=fake) as run, \
             mock.patch("writing_loop.time.sleep"), \
             mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(RuntimeError) as ctx:
                writing_loop.call_claude("x", "y", "sonnet", retries=2)
        self.assertIn("persistent boom", str(ctx.exception))
        self.assertEqual(run.call_count, 3)

    def test_retries_on_timeout(self):
        results = [
            subprocess.TimeoutExpired(cmd="claude", timeout=1),
            mock.Mock(returncode=0, stdout="ok after timeout", stderr=""),
        ]
        with mock.patch("writing_loop.subprocess.run", side_effect=results), \
             mock.patch("writing_loop.time.sleep"), \
             mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(
                writing_loop.call_claude("x", "y", "sonnet", retries=2),
                "ok after timeout",
            )

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
            with self.assertRaises(RuntimeError):
                writing_loop.call_claude("x", "y", "sonnet")
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

    def test_quiet_mode_suppresses_retry_prints(self):
        results = [
            mock.Mock(returncode=1, stdout="", stderr="blip"),
            mock.Mock(returncode=0, stdout="ok", stderr=""),
        ]
        buf = io.StringIO()
        with mock.patch("writing_loop.subprocess.run", side_effect=results), \
             mock.patch("writing_loop.time.sleep"), \
             mock.patch("sys.stdout", buf):
            writing_loop.call_claude("x", "y", "sonnet", retries=2, quiet=True)
        self.assertNotIn("Retrying", buf.getvalue())


# ---------------- save_final (with format) ----------------

class SaveFinalTests(unittest.TestCase):
    def test_writes_to_log_dir_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            writing_loop.save_final("the draft", None, Path(tmp))
            self.assertEqual((Path(tmp) / "final_draft.md").read_text(), "the draft")

    def test_writes_md_output_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()
            out = Path(tmp) / "out.md"
            writing_loop.save_final("the draft", str(out), log_dir, output_format="md")
            self.assertEqual(out.read_text(), "the draft")

    def test_writes_html_output_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()
            out = Path(tmp) / "out.html"
            writing_loop.save_final("hello <world>", str(out), log_dir, output_format="html")
            content = out.read_text()
            self.assertIn("<!DOCTYPE html>", content)
            self.assertIn("&lt;world&gt;", content)
            # Raw log stays raw
            self.assertEqual((log_dir / "final_draft.md").read_text(), "hello <world>")


# ---------------- integration loop ----------------

class RunLoopIntegrationTests(unittest.TestCase):
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
            code = self._run(tmp, ["DRAFT ONE", FEEDBACK_RESPONSE_1, "DRAFT TWO", APPROVED_RESPONSE])
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
                plateau_window=0,  # disable plateau so we fall through to max
            )
            self.assertEqual(code, 2)

    def test_stops_on_plateau(self):
        """Three identical scores in a row triggers plateau exit."""
        outputs = [
            "D1", "SCORE: 6/10\n1. fix x",
            "D2", "SCORE: 6/10\n1. fix y",
            "D3", "SCORE: 6/10\n1. fix z",
            "D4", "SCORE: 6/10\n1. fix w",  # extra buffer
        ]
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run(tmp, outputs, max_iterations=10, plateau_window=3)
            self.assertEqual(code, 3)
            run_dir = next(Path(tmp).iterdir())
            # Should have 3 iterations of editor output then stopped
            self.assertIn("iter003_editor_output.md", {p.name for p in run_dir.iterdir()})
            self.assertNotIn("iter004_editor_output.md", {p.name for p in run_dir.iterdir()})

    def test_approve_above_threshold_accepts_below_literal_approval(self):
        """A 9/10 score with no APPROVED token still ends the loop when --approve-above 8."""
        outputs = ["D1", "SCORE: 9/10\n1. minor polish only"]
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run(tmp, outputs, approve_above=8)
            self.assertEqual(code, 0)

    def test_approve_above_does_not_trigger_below_threshold(self):
        outputs = ["D1", "SCORE: 7/10\n1. tighten"] * 3
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run(
                tmp,
                [o for pair in zip(*[iter(outputs)] * 2) for o in pair],
                approve_above=9,
                max_iterations=2,
                plateau_window=0,
            )
            self.assertEqual(code, 2)

    def test_writer_receives_accumulated_feedback_history(self):
        captured_writer_inputs: list[str] = []
        script = iter([
            "DRAFT_1", FEEDBACK_RESPONSE_1,
            "DRAFT_2", FEEDBACK_RESPONSE_2,
            "DRAFT_3", APPROVED_RESPONSE,
        ])

        def fake_call(user_prompt, system_prompt, model, **kwargs):
            if "writer" in system_prompt.lower():
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
        iter3_input = captured_writer_inputs[2]
        self.assertIn("Weak opening", iter3_input)
        self.assertIn("Tighten paragraph 3", iter3_input)
        self.assertIn("already addressed", iter3_input)

    def test_editor_receives_its_own_prior_feedback(self):
        captured_editor_inputs: list[str] = []
        script = iter([
            "DRAFT_1", FEEDBACK_RESPONSE_1,
            "DRAFT_2", FEEDBACK_RESPONSE_2,
            "DRAFT_3", APPROVED_RESPONSE,
        ])

        def fake_call(user_prompt, system_prompt, model, **kwargs):
            if "Please review" in user_prompt:
                captured_editor_inputs.append(user_prompt)
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
        self.assertIn("Weak opening", captured_editor_inputs[1])
        self.assertIn("Weak opening", captured_editor_inputs[2])
        self.assertIn("Tighten paragraph 3", captured_editor_inputs[2])

    def test_input_draft_skips_writer_on_iteration_1(self):
        writer_calls: list[str] = []
        editor_calls: list[str] = []

        def fake_call(user_prompt, system_prompt, model, **kwargs):
            if "writer" in system_prompt.lower():
                writer_calls.append(user_prompt)
                return f"WRITER_{len(writer_calls)}"
            editor_calls.append(user_prompt)
            return APPROVED_RESPONSE if len(editor_calls) >= 2 else FEEDBACK_RESPONSE_1

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=fake_call):
            code = writing_loop.run_loop(
                prompt="brief",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
                input_draft="MY_STARTING_DRAFT",
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(writer_calls), 1)
            self.assertIn("MY_STARTING_DRAFT", editor_calls[0])
            self.assertIn("MY_STARTING_DRAFT", writer_calls[0])

    def test_input_draft_approved_immediately(self):
        writer_calls: list[str] = []

        def fake_call(user_prompt, system_prompt, model, **kwargs):
            if "writer" in system_prompt.lower():
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

    def test_score_trajectory_in_output(self):
        buf = io.StringIO()
        outputs = iter(["D1", FEEDBACK_RESPONSE_1, "D2", FEEDBACK_RESPONSE_2, "D3", APPROVED_RESPONSE])
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
        self.assertIn("4/10 → 7/10 → 10/10 ✓", out)

    def test_target_words_reaches_editor(self):
        captured_editor_inputs: list[str] = []

        def fake_call(user_prompt, system_prompt, model, **kwargs):
            if "Please review" in user_prompt:
                captured_editor_inputs.append(user_prompt)
                return APPROVED_RESPONSE
            return "SHORT DRAFT"

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=fake_call):
            writing_loop.run_loop(
                prompt="x",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
                target_words=500,
            )
        self.assertIn("Target length", captured_editor_inputs[0])
        self.assertIn("500", captured_editor_inputs[0])

    def test_style_reaches_system_prompts(self):
        captured_system_prompts: list[str] = []

        def fake_call(user_prompt, system_prompt, model, **kwargs):
            captured_system_prompts.append(system_prompt)
            return APPROVED_RESPONSE if "editor" in system_prompt.lower() else "D"

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", io.StringIO()), \
             mock.patch("writing_loop.call_claude", side_effect=fake_call):
            writing_loop.run_loop(
                prompt="x",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
                style="fiction",
                strictness="harsh",
            )
        writer_sys = captured_system_prompts[0]
        editor_sys = captured_system_prompts[1]
        self.assertIn("showing over telling", writer_sys)
        self.assertIn("Atlantic", editor_sys)
        self.assertIn("showing over telling", editor_sys)  # style reaches editor too

    def test_quiet_mode_suppresses_progress(self):
        buf = io.StringIO()
        outputs = iter(["D1", APPROVED_RESPONSE])
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
                quiet=True,
            )
        self.assertEqual(buf.getvalue(), "")

    def test_json_mode_emits_structured_summary(self):
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
                verbose=False,
                json_output=True,
            )
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "approved")
        self.assertEqual(payload["iterations"], 2)
        self.assertEqual(payload["scores"], [4, 10])
        self.assertEqual(payload["final_draft"], "D2")
        self.assertIn("log_dir", payload)

    def test_json_mode_on_max_iterations(self):
        buf = io.StringIO()
        outputs = iter(["D1", "SCORE: 5/10\n1. meh"] * 5)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", buf), \
             mock.patch("writing_loop.call_claude", side_effect=lambda *a, **k: next(outputs)):
            code = writing_loop.run_loop(
                prompt="x",
                max_iterations=2,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
                json_output=True,
                plateau_window=0,  # disable plateau
            )
        payload = json.loads(buf.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "max_iterations")
        self.assertEqual(len(payload["scores"]), 2)

    def test_json_mode_on_plateau(self):
        buf = io.StringIO()
        outputs = iter([
            "D1", "SCORE: 5/10\n1. a",
            "D2", "SCORE: 5/10\n1. b",
            "D3", "SCORE: 5/10\n1. c",
            "D4", "SCORE: 5/10\n1. d",
        ])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", buf), \
             mock.patch("writing_loop.call_claude", side_effect=lambda *a, **k: next(outputs)):
            code = writing_loop.run_loop(
                prompt="x",
                max_iterations=10,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
                json_output=True,
                plateau_window=3,
            )
        payload = json.loads(buf.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(payload["status"], "plateau")

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

    def test_output_html_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "essay.html"
            code = self._run(
                tmp,
                ["Paragraph one.\n\nParagraph two with <tag>.", APPROVED_RESPONSE],
                output_path=str(out_path),
                output_format="html",
            )
            self.assertEqual(code, 0)
            content = out_path.read_text()
            self.assertIn("<!DOCTYPE html>", content)
            self.assertIn("&lt;tag&gt;", content)

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

    def test_keyboard_interrupt_with_json_mode(self):
        """Interrupt in json mode still emits a JSON summary."""
        def boom(*a, **k):
            raise KeyboardInterrupt()
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("sys.stdout", buf), \
             mock.patch("writing_loop.call_claude", side_effect=boom):
            code = writing_loop.run_loop(
                prompt="x",
                max_iterations=5,
                writer_model="sonnet",
                editor_model="sonnet",
                log_dir=Path(tmp),
                output_path=None,
                verbose=False,
                json_output=True,
            )
        self.assertEqual(code, 130)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "interrupted")


# ---------------- main() entrypoint ----------------

class MainEntrypointTests(unittest.TestCase):
    def test_main_passes_args_through(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("writing_loop.run_loop", return_value=0) as rl:
            code = writing_loop.main([
                "topic",
                "--max-iterations", "3",
                "--writer-model", "opus",
                "--editor-model", "haiku",
                "--log-dir", tmp,
                "--output", str(Path(tmp) / "out.md"),
                "--verbose",
                "--style", "technical",
                "--strictness", "lenient",
                "--target-words", "300",
                "--approve-above", "8",
                "--plateau-window", "4",
                "--format", "html",
                "--quiet",
                "--json",
            ])
        self.assertEqual(code, 0)
        kwargs = rl.call_args.kwargs
        self.assertEqual(kwargs["prompt"], "topic")
        self.assertEqual(kwargs["style"], "technical")
        self.assertEqual(kwargs["strictness"], "lenient")
        self.assertEqual(kwargs["target_words"], 300)
        self.assertEqual(kwargs["approve_above"], 8)
        self.assertEqual(kwargs["plateau_window"], 4)
        self.assertEqual(kwargs["output_format"], "html")
        self.assertTrue(kwargs["quiet"])
        self.assertTrue(kwargs["json_output"])

    def test_main_reads_prompt_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("file-based prompt\n")
            path = f.name
        try:
            with mock.patch("writing_loop.run_loop", return_value=0) as rl:
                writing_loop.main(["--prompt-file", path])
            self.assertEqual(rl.call_args.kwargs["prompt"], "file-based prompt")
        finally:
            Path(path).unlink()

    def test_main_reads_input_draft(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("my draft\n")
            path = f.name
        try:
            with mock.patch("writing_loop.run_loop", return_value=0) as rl:
                writing_loop.main(["topic", "--input-draft", path])
            self.assertEqual(rl.call_args.kwargs["input_draft"], "my draft")
        finally:
            Path(path).unlink()

    def test_main_input_draft_without_prompt_uses_fallback(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("orphan draft")
            path = f.name
        try:
            with mock.patch("writing_loop.run_loop", return_value=0) as rl:
                writing_loop.main(["--input-draft", path])
            self.assertIn("Polish", rl.call_args.kwargs["prompt"])
        finally:
            Path(path).unlink()

    def test_main_errors_without_prompt_or_draft(self):
        with self.assertRaises(SystemExit):
            writing_loop.main([])

    def test_main_rejects_zero_iterations(self):
        with self.assertRaises(SystemExit):
            writing_loop.main(["topic", "--max-iterations", "0"])

    def test_main_rejects_approve_above_out_of_range(self):
        with self.assertRaises(SystemExit):
            writing_loop.main(["topic", "--approve-above", "11"])
        with self.assertRaises(SystemExit):
            writing_loop.main(["topic", "--approve-above", "0"])

    def test_main_rejects_negative_plateau_window(self):
        with self.assertRaises(SystemExit):
            writing_loop.main(["topic", "--plateau-window", "-1"])

    def test_main_expands_log_dir_tilde(self):
        with mock.patch("writing_loop.run_loop", return_value=0) as rl:
            writing_loop.main(["topic", "--log-dir", "~/myloops"])
        self.assertFalse(str(rl.call_args.kwargs["log_dir"]).startswith("~"))


class PromptConstantsTests(unittest.TestCase):
    def test_writer_base_prompt_is_nonempty(self):
        self.assertIn("writer", writing_loop.BASE_WRITER_SYSTEM_PROMPT.lower())

    def test_editor_base_prompt_requires_approved_token(self):
        self.assertIn("APPROVED", writing_loop.BASE_EDITOR_SYSTEM_PROMPT)

    def test_editor_base_prompt_requires_score(self):
        self.assertIn("SCORE:", writing_loop.BASE_EDITOR_SYSTEM_PROMPT)

    def test_style_addons_has_all_choices(self):
        for style in ("default", "academic", "journalistic", "fiction", "technical", "blog", "persuasive"):
            self.assertIn(style, writing_loop.STYLE_ADDONS)

    def test_strictness_addons_has_all_choices(self):
        for s in ("lenient", "standard", "harsh"):
            self.assertIn(s, writing_loop.STRICTNESS_ADDONS)


if __name__ == "__main__":
    unittest.main()
