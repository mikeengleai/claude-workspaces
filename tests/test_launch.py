"""Launch and create behaviour of cw, with tmux and claude replaced by recorders.

Runs against the repository copy by default, or the copy named in CW_UNDER_TEST
(the installer points it at the installed file, so the self-test proves what was
installed, not what was in the checkout).

    python3 -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
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
        self.cw.AUTOSTART_FILE = root / "workspace-autostart.json"
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


class AutostartTest(Base):
    """Workspaces flagged for autostart are resumed by `cw boot` after a reboot."""

    def setUp(self):
        super().setUp()
        (self.cw.WORKSPACES_DIR / "other").mkdir()
        (self.cw.WORKSPACES_DIR / "other" / "CLAUDE.md").write_text("# Other\n\nnot flagged\n")
        self.network_waits = []
        self.cw.wait_for_network = lambda secs: self.network_waits.append(secs) or True
        self.cw.BOOT_SETTLE_SECS = 0
        # Every boot path reads ~ (tool dirs, transcripts, settings.json): never the real one.
        self.home = Path(self.tmp.name)
        for patcher in (mock.patch.object(self.cw.Path, "home", lambda: self.home),
                        mock.patch.dict(os.environ, {"HOME": str(self.home)})):  # expanduser reads $HOME
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_transcript(self, name):
        project = self.cw.workspace_memory_dir(name).parent
        project.mkdir(parents=True, exist_ok=True)
        (project / "s.jsonl").write_text("{}\n")

    def flagged(self):
        return self.cw.load_autostart()

    def test_on_records_the_flag(self):
        _, code, _ = self.main("autostart", "proj", "on")
        self.assertEqual(code, 0)
        self.assertEqual(self.flagged(), ["proj"])

    def test_off_clears_it(self):
        self.main("autostart", "proj", "on")
        _, code, _ = self.main("autostart", "proj", "off")
        self.assertEqual(code, 0)
        self.assertEqual(self.flagged(), [])

    def test_unknown_workspace_is_refused_and_nothing_written(self):
        _, code, _ = self.main("autostart", "nope", "on")
        self.assertEqual(code, 1)
        self.assertFalse(self.cw.AUTOSTART_FILE.exists())

    def test_bare_autostart_lists_the_flagged(self):
        self.main("autostart", "proj", "on")
        _, code, out = self.main("autostart")
        self.assertEqual(code, 0)
        self.assertIn("proj", out)
        self.assertNotIn("other", out)

    def test_list_marks_flagged_workspaces(self):
        self.main("autostart", "proj", "on")
        _, _, out = self.main("list")
        line = {l.split()[0]: l for l in out.splitlines() if l.strip()}
        self.assertIn("autostart", line["proj"])
        self.assertNotIn("autostart", line["other"])

    def test_a_launch_does_not_clear_the_flag(self):
        # Launches rewrite the sessions entry wholesale, which is why the flag lives apart.
        self.main("autostart", "proj", "on")
        self.main("launch", "proj", "--detach")
        self.assertEqual(self.flagged(), ["proj"])

    def test_boot_resumes_only_flagged_with_rc_detached(self):
        self.main("autostart", "proj", "on")
        rec, code, _ = self.main("boot")
        self.assertEqual(code, 0)
        made = [c for c in rec.calls if c[:2] == ["tmux", "new-session"]]
        self.assertEqual(len(made), 1)
        self.assertIn("cw-proj", made[0])
        i = made[0].index("--remote-control")
        self.assertEqual(made[0][i + 1], "proj")
        self.assertEqual(self.execs, [], "boot must never attach")

    def test_boot_resumes_the_previous_conversation_not_a_fresh_one(self):
        self.main("autostart", "proj", "on")
        self.main("launch", "proj", "--detach")  # records last_launched
        self.add_transcript("proj")
        rec, _, _ = self.main("boot")
        cmd = rec.new_session()
        self.assertIn("--resume", cmd)
        self.assertFalse(any("handoff" in a.lower() for a in cmd), "no seed prompt: it sits idle")

    def test_resume_picks_the_newest_transcript_by_id(self):
        # Not --continue: in a git worktree claude's --continue finds no conversation
        # and exits ("No conversation found to continue"), though the transcripts are
        # right there. Found 2026-10-18 on two worktree workspaces at the first cw boot.
        self.main("autostart", "proj", "on")
        project = self.cw.workspace_memory_dir("proj").parent
        project.mkdir(parents=True)
        (project / "older.jsonl").write_text("{}\n")
        (project / "newest.jsonl").write_text("{}\n")
        os.utime(project / "older.jsonl", (1000, 1000))
        rec, _, _ = self.main("boot")
        cmd = rec.new_session()
        self.assertNotIn("--continue", cmd)
        i = cmd.index("--resume")
        self.assertEqual(cmd[i + 1], "newest")

    def test_boot_continues_even_when_the_sessions_entry_is_gone(self):
        # A reset without relaunch, a rotate or an archive restore drops the entry but
        # the transcript is there: boot must not open an empty conversation.
        self.main("autostart", "proj", "on")
        self.add_transcript("proj")
        self.assertFalse(self.cw.SESSIONS_FILE.exists())
        rec, _, _ = self.main("boot")
        self.assertIn("--resume", rec.new_session())

    def test_boot_with_no_transcript_starts_fresh_rather_than_failing(self):
        self.main("autostart", "proj", "on")
        rec, code, _ = self.main("boot")
        self.assertEqual(code, 0)
        self.assertNotIn("--resume", rec.new_session())

    def test_boot_reports_a_session_that_dies_straight_after_starting(self):
        self.main("autostart", "proj", "on")
        rec = Recorder()
        real_run = rec.run
        def run(cmd, *a, **kw):  # the session appears, then claude exits during the settle
            r = real_run(cmd, *a, **kw)
            if cmd[:2] == ["tmux", "has-session"] and rec.calls.count(cmd) >= 2:
                rec.sessions.discard("cw-proj")
            return r
        rec.run = run
        out = io.StringIO()
        with mock.patch.object(self.cw.subprocess, "run", run), \
             mock.patch.object(self.cw.os, "execvp", lambda f, a: self.execs.append(a)), \
             mock.patch.object(self.cw.sys, "argv", ["cw", "boot"]), redirect_stdout(out):
            with self.assertRaises(SystemExit) as e:
                self.cw.main()
        self.assertEqual(e.exception.code, 1)
        self.assertIn("exited within", out.getvalue())

    def test_boot_keeps_going_when_tmux_cannot_run(self):
        self.main("autostart", "proj", "on")
        self.main("autostart", "other", "on")
        rec = Recorder()
        def run(cmd, *a, **kw):
            if cmd[:2] == ["tmux", "new-session"] and "cw-other" in cmd:
                raise FileNotFoundError(2, "No such file or directory", "tmux")
            return rec.run(cmd, *a, **kw)
        out = io.StringIO()
        with mock.patch.object(self.cw.subprocess, "run", run), \
             mock.patch.object(self.cw.os, "execvp", lambda f, a: self.execs.append(a)), \
             mock.patch.object(self.cw.sys, "argv", ["cw", "boot"]), redirect_stdout(out):
            with self.assertRaises(SystemExit) as e:
                self.cw.main()
        self.assertEqual(e.exception.code, 1)
        self.assertIn("cw-proj", rec.sessions, "the other flagged workspace must still start")

    def test_network_probe_follows_claudes_configured_endpoint(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://gateway.internal:4100"}}))
        with mock.patch.dict(self.cw.os.environ, {}, clear=True):
            self.assertEqual(self.cw.claude_endpoint(), ("gateway.internal", 4100))
        with mock.patch.dict(self.cw.os.environ, {"ANTHROPIC_BASE_URL": "https://x.example"}, clear=True):
            self.assertEqual(self.cw.claude_endpoint(), ("x.example", 443))
        (self.home / ".claude" / "settings.json").write_text("{}")
        with mock.patch.dict(self.cw.os.environ, {}, clear=True):
            self.assertEqual(self.cw.claude_endpoint(), ("api.anthropic.com", 443))

    def trust(self, path, accepted=True):
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {str(Path(path).resolve()): {"hasTrustDialogAccepted": accepted}}}))

    def test_flagging_an_untrusted_workspace_warns(self):
        self.trust(self.cw.WORKSPACES_DIR / "other")
        _, code, out = self.main("autostart", "proj", "on")
        self.assertEqual(code, 0)
        self.assertIn("trust prompt", out)

    def test_trust_on_a_parent_folder_counts(self):
        self.trust(self.cw.WORKSPACES_DIR)
        _, _, out = self.main("autostart", "proj", "on")
        self.assertNotIn("trust prompt", out)

    def test_parent_trust_does_not_reach_into_a_git_repo(self):
        # Seen 2026-10-18: ~/workspaces was trusted, yet two workspaces that are git repos
        # (with no trust entry of their own) came up at the trust prompt; non-repos did not.
        self.trust(self.cw.WORKSPACES_DIR)
        (self.cw.WORKSPACES_DIR / "proj" / ".git").mkdir()
        _, _, out = self.main("autostart", "proj", "on")
        self.assertIn("trust prompt", out)

    def test_a_git_repo_trusted_at_its_own_root_is_fine(self):
        (self.cw.WORKSPACES_DIR / "proj" / ".git").mkdir()
        self.trust(self.cw.WORKSPACES_DIR / "proj")
        _, _, out = self.main("autostart", "proj", "on")
        self.assertNotIn("trust prompt", out)

    def test_a_worktree_inherits_its_main_repos_trust(self):
        main = self.cw.WORKSPACES_DIR / "other"
        (main / ".git" / "worktrees" / "proj").mkdir(parents=True)
        (self.cw.WORKSPACES_DIR / "proj" / ".git").write_text(f"gitdir: {main}/.git/worktrees/proj\n")
        self.trust(main)
        _, _, out = self.main("autostart", "proj", "on")
        self.assertNotIn("trust prompt", out)

    def test_unreadable_claude_state_does_not_warn(self):
        _, _, out = self.main("autostart", "proj", "on")
        self.assertNotIn("trust prompt", out)

    def test_boot_says_which_workspace_will_wait_at_the_trust_prompt(self):
        self.main("autostart", "proj", "on")
        self.trust(self.cw.WORKSPACES_DIR / "proj", accepted=False)
        _, _, out = self.main("boot")
        self.assertIn("'proj' is waiting at claude's folder-trust prompt", out)

    def test_a_corrupt_flag_file_does_not_break_list(self):
        self.cw.AUTOSTART_FILE.write_text("{not json")
        _, code, out = self.main("list")
        self.assertEqual(code, 0)
        self.assertIn("proj", out)

    def test_boot_leaves_running_sessions_alone(self):
        self.main("autostart", "proj", "on")
        rec, code, _ = self.main("boot", running={"cw-proj"})
        self.assertEqual(code, 0)
        self.assertIsNone(rec.new_session())

    def test_boot_with_nothing_flagged_does_nothing(self):
        rec, code, _ = self.main("boot")
        self.assertEqual(code, 0)
        self.assertIsNone(rec.new_session())

    def test_a_flagged_workspace_that_is_gone_is_warned_about_not_failed(self):
        # Failing would leave the boot unit red forever over a deliberate removal.
        self.main("autostart", "proj", "on")
        self.main("autostart", "other", "on")
        shutil.rmtree(self.cw.WORKSPACES_DIR / "other")
        rec, code, out = self.main("boot")
        self.assertEqual(code, 0)
        self.assertIn("no longer exists", out)
        self.assertIn("cw-proj", rec.new_session())

    def test_boot_waits_for_the_network_first(self):
        self.main("autostart", "proj", "on")
        self.main("boot", "--wait-network", "120")
        self.assertEqual(self.network_waits, [120])

    def test_boot_puts_the_usual_tool_dirs_on_path(self):
        # launchd and systemd start with a bare PATH; claude and tmux live elsewhere.
        (self.home / ".local" / "bin").mkdir(parents=True)
        self.main("autostart", "proj", "on")
        env_seen = {}
        orig = self.cw.launch_workspace
        def spy(*a, **kw):
            env_seen["PATH"] = self.cw.os.environ.get("PATH", "")
            return orig(*a, **kw)
        self.cw.launch_workspace = spy
        self.main("boot")
        self.assertIn(str(self.home / ".local" / "bin"), env_seen["PATH"].split(os.pathsep))

    def test_rename_carries_the_flag(self):
        self.main("autostart", "proj", "on")
        self.main("rename", "proj", "renamed")
        self.assertEqual(self.flagged(), ["renamed"])

    def test_shelve_clears_the_flag(self):
        self.main("autostart", "proj", "on")
        with mock.patch.object(self.cw, "archive_workspace", lambda n: None):
            self.main("shelve", "proj")
        self.assertEqual(self.flagged(), [])

    def test_delete_clears_the_flag(self):
        self.main("autostart", "proj", "on")
        with mock.patch("builtins.input", lambda *_: "y"):
            self.main("delete", "proj")
        self.assertEqual(self.flagged(), [])


if __name__ == "__main__":
    unittest.main()
