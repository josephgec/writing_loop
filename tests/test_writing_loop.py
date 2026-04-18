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


class GenerateRunIdTests(unittest.TestCase):
    def test_format_is_timestamp(self):
        run_id = writing_loop.generate_run_id()
        self.assertRegex(run_id, r"^\d{8}_\d{6}$")

    def test_ids_differ_across_calls(self):
        a = writing_loop.generate_run_id()
        # Force a tick by patching datetime for determinism
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


class IsApprovedTests(unittest.TestCase):
    def test_approved_exact(self):
        self.assertTrue(writing_loop.is_approved("APPROVED\nGreat piece."))

    def test_approved_with_leading_whitespace(self):
        self.assertTrue(writing_loop.is_approved("   APPROVED\nnice."))

    def test_not_approved_when_in_middle(self):
        self.assertFalse(writing_loop.is_approved("1. Tighten opening.\nAPPROVED"))

    def test_not_approved_when_substring(self):
        self.assertFalse(writing_loop.is_approved("APPROVEDISH"))

    def test_empty_string(self):
        self.assertFalse(writing_loop.is_approved(""))

    def test_feedback_list(self):
        feedback = "1. The opening is weak.\n2. Cut clichés in paragraph 3."
        self.assertFalse(writing_loop.is_approved(feedback))


class BuildInputsTests(unittest.TestCase):
    def test_writer_first_iteration_uses_prompt_only(self):
        out = writing_loop.build_writer_input("Topic X", 1, None, None)
        self.assertIn("Topic X", out)
        self.assertNotIn("previous draft", out)

    def test_writer_later_iteration_includes_draft_and_feedback(self):
        out = writing_loop.build_writer_input("Topic X", 2, "DRAFT_TEXT", "FEEDBACK_TEXT")
        self.assertIn("DRAFT_TEXT", out)
        self.assertIn("FEEDBACK_TEXT", out)
        self.assertIn("previous draft", out)

    def test_editor_input_includes_prompt_and_draft(self):
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
        ])
        self.assertEqual(args.max_iterations, 3)
        self.assertEqual(args.writer_model, "opus")
        self.assertEqual(args.editor_model, "haiku")
        self.assertEqual(args.output, "out.md")
        self.assertEqual(args.log_dir, "/tmp/logs")
        self.assertTrue(args.verbose)


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

    def test_raises_on_nonzero_exit(self):
        fake_result = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch("writing_loop.subprocess.run", return_value=fake_result):
            with self.assertRaises(RuntimeError) as ctx:
                writing_loop.call_claude("x", "y", "sonnet")
        self.assertIn("boom", str(ctx.exception))

    def test_raises_when_claude_not_found(self):
        with mock.patch("writing_loop.subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(RuntimeError) as ctx:
                writing_loop.call_claude("x", "y", "sonnet")
        self.assertIn("claude", str(ctx.exception).lower())

    def test_raises_on_timeout(self):
        with mock.patch(
            "writing_loop.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=1),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                writing_loop.call_claude("x", "y", "sonnet")
        self.assertIn("timed out", str(ctx.exception).lower())


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

    def _run_with_outputs(self, tmp, outputs_list, *, capture_stdout=True, **overrides):
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
        stdout_patch = mock.patch("sys.stdout", io.StringIO()) if capture_stdout else mock.patch("sys.stdout", sys.stdout)
        with stdout_patch, mock.patch("writing_loop.call_claude", side_effect=lambda *a, **k: next(outputs)):
            return writing_loop.run_loop(**kwargs)

    def test_stops_when_editor_approves(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run_with_outputs(
                tmp,
                [
                    "DRAFT ONE",
                    "1. Weak opening.\n2. Clichés in para 2.",
                    "DRAFT TWO",
                    "APPROVED\nStrong and clear.",
                ],
            )
            self.assertEqual(code, 0)
            run_dirs = list(Path(tmp).iterdir())
            files = {p.name for p in run_dirs[0].iterdir()}
            self.assertIn("iter002_APPROVED.md", files)
            self.assertEqual((run_dirs[0] / "final_draft.md").read_text(), "DRAFT TWO")

    def test_stops_after_max_iterations(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run_with_outputs(
                tmp,
                ["never-approved"] * 10,
                prompt="x",
                max_iterations=2,
            )
            self.assertEqual(code, 2)
            run_dirs = list(Path(tmp).iterdir())
            self.assertEqual(len(run_dirs), 1)
            files = {p.name for p in run_dirs[0].iterdir()}
            self.assertIn("final_draft.md", files)
            self.assertIn("iter001_writer_output.md", files)
            self.assertIn("iter002_editor_output.md", files)

    def test_verbose_prints_drafts_and_feedback(self):
        buf = io.StringIO()
        outputs = iter(["D1", "FEEDBACK_XYZ", "D2", "APPROVED\nok"])
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
        self.assertIn("FEEDBACK_XYZ", out)
        self.assertIn("--- Draft ---", out)
        self.assertIn("--- Feedback ---", out)

    def test_output_path_receives_final_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "essay.md"
            code = self._run_with_outputs(
                tmp,
                ["FINAL DRAFT", "APPROVED\nnice."],
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
            run_dirs = list(Path(tmp).iterdir())
            final = (run_dirs[0] / "final_draft.md").read_text()
            self.assertEqual(final, "DRAFT_BEFORE_INTERRUPT")

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
            run_dirs = list(Path(tmp).iterdir())
            self.assertNotIn("final_draft.md", {p.name for p in run_dirs[0].iterdir()})


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


if __name__ == "__main__":
    unittest.main()
