"""Launch and create behaviour of cw, with tmux and claude replaced by recorders.

Runs against the repository copy by default, or the copy named in CW_UNDER_TEST
(the installer points it at the installed file, so the self-test proves what was
installed, not what was in the checkout).

    python3 -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
CW = Path(os.environ.get("CW_UNDER_TEST") or HERE.parent / "claude-workspaces")


def load_cw():
    loader = importlib.machinery.SourceFileLoader("cw_under_test", str(CW))
    spec = importlib.util.spec_from_loader("cw_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Recorder:
    """Stands in for tmux: remembers sessions it was asked to create."""

    def __init__(self, running=()):
        self.sessions = set(running)
        self.calls = []

    def run(self, cmd, *a, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ["tmux", "new-session"]:
            self.sessions.add(cmd[cmd.index("-s") + 1])
        rc = 0
        if cmd[:2] == ["tmux", "has-session"]:
            rc = 0 if cmd[-1].lstrip("=") in self.sessions else 1
        return mock.Mock(returncode=rc, stdout="", stderr="")

    def new_session(self):
        found = [c for c in self.calls if c[:2] == ["tmux", "new-session"]]
        return found[-1] if found else None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cw = load_cw()
        self.cw.WORKSPACES_DIR = root / "workspaces"
        self.cw.SESSIONS_FILE = root / "workspace-sessions.json"
        self.cw.WORKSPACES_DIR.mkdir()
        (self.cw.WORKSPACES_DIR / "proj").mkdir()
        (self.cw.WORKSPACES_DIR / "proj" / "CLAUDE.md").write_text("# Proj\n\nmine\n")
        self.execs = []

    def tearDown(self):
        self.tmp.cleanup()

    def main(self, *argv, running=()):
        rec = Recorder(running)
        out = io.StringIO()
        code = 0
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}  # attach, not switch-client
        with mock.patch.dict(self.cw.os.environ, env, clear=True), \
             mock.patch.object(self.cw.subprocess, "run", rec.run), \
             mock.patch.object(self.cw.os, "execvp", lambda f, a: self.execs.append(a)), \
             mock.patch.object(self.cw.sys, "argv", ["cw", *argv]), redirect_stdout(out):
            try:
                self.cw.main()
            except SystemExit as e:
                code = e.code
        return rec, code, out.getvalue()


class LaunchTest(Base):
    def test_rc_detach_starts_tmux_with_remote_control_and_does_not_attach(self):
        rec, code, _ = self.main("launch", "proj", "--rc", "--detach")
        self.assertEqual(code, 0)
        cmd = rec.new_session()
        self.assertIsNotNone(cmd, "no tmux session was created")
        self.assertIn("cw-proj", cmd)
        i = cmd.index("--remote-control")
        self.assertEqual(cmd[i + 1], "proj")
        self.assertEqual(self.execs, [], "--detach must not attach")

    def test_top_level_flags_work_too(self):
        rec, code, _ = self.main("--rc", "--detach", "launch", "proj")
        self.assertEqual(code, 0)
        self.assertIn("--remote-control", rec.new_session())
        self.assertEqual(self.execs, [])

    def test_permissions_are_still_bypassed(self):
        # cw's sessions run unattended; owner decision 2026-10-14 keeps the bypass.
        rec, _, _ = self.main("launch", "proj", "--rc", "--detach")
        self.assertIn("--dangerously-skip-permissions", rec.new_session())

    def test_default_launch_is_unchanged(self):
        rec, _, _ = self.main("launch", "proj")
        cmd = rec.new_session()
        self.assertNotIn("--remote-control", cmd)
        self.assertEqual(len(self.execs), 1, "a plain launch attaches")
        self.assertEqual(self.execs[0][:2], ["tmux", "attach-session"])

    def test_detach_leaves_a_running_session_alone(self):
        rec, code, out = self.main("launch", "proj", "--rc", "--detach", running={"cw-proj"})
        self.assertEqual(code, 0)
        # Not vacuous: an older cw also creates nothing, by rejecting the flags.
        self.assertIn("already running", out)
        self.assertIsNone(rec.new_session())
        self.assertEqual(self.execs, [])

    def test_detach_without_tmux_is_refused(self):
        _, code, _ = self.main("launch", "proj", "--detach", "--no-tmux")
        self.assertEqual(code, 2)
        self.assertEqual(self.execs, [])

    def test_missing_workspace_exits_nonzero(self):
        _, code, _ = self.main("launch", "nope", "--detach")
        self.assertEqual(code, 1)


class CreateTest(Base):
    def test_create_refuses_to_overwrite_claude_md(self):
        _, code, _ = self.main("create", "proj", "something else")
        self.assertEqual(code, 1)
        self.assertEqual((self.cw.WORKSPACES_DIR / "proj" / "CLAUDE.md").read_text(), "# Proj\n\nmine\n")

    def test_create_makes_a_new_workspace(self):
        _, code, _ = self.main("create", "fresh-one", "A description")
        self.assertEqual(code, 0)
        self.assertIn("A description", (self.cw.WORKSPACES_DIR / "fresh-one" / "CLAUDE.md").read_text())


if __name__ == "__main__":
    unittest.main()
