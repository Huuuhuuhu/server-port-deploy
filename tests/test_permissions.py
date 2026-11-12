from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_permissions.py"
LINUX = sys.platform.startswith("linux")
ROOT = LINUX and os.geteuid() == 0


class PlatformTests(unittest.TestCase):
    @unittest.skipIf(LINUX, "non-Linux refusal")
    def test_other_platforms_cannot_claim_linux_permission_success(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--expected-user", "app",
                                 "--read-only", str(SCRIPT)], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")

    @unittest.skipUnless(LINUX and not ROOT, "Linux non-root invocation")
    def test_protected_system_file_and_own_data_directory(self):
        import pwd
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(SCRIPT), "--expected-user",
                                     pwd.getpwuid(os.geteuid()).pw_name,
                                     "--read-only", "/usr/bin/true", "--writable", temporary],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(json.loads(result.stdout)["ok"])


@unittest.skipUnless(ROOT, "root in isolated Linux test environment required")
class PermissionIntegrationTests(unittest.TestCase):
    def setUp(self):
        import pwd
        self.account = pwd.getpwnam("nobody")
        self.temp = tempfile.TemporaryDirectory(prefix="spd-permissions-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.base.chmod(0o755)
        self.script = self.base / "check_permissions.py"
        shutil.copyfile(SCRIPT, self.script)
        self.script.chmod(0o644)
        self.code = self.base / "app"
        self.code.mkdir(mode=0o755)
        self.source = self.file(self.code / "app.py")
        self.tools = self.base / "tools"
        (self.tools / "scripts").mkdir(parents=True, mode=0o755)
        (self.tools / "references").mkdir(mode=0o755)
        self.tools.chmod(0o755)
        self.tool = self.file(self.tools / "scripts/registry.py")
        self.template = self.file(self.tools / "references/server-deployments-template.md")
        self.data = self.base / "data"
        self.data.mkdir(mode=0o700)
        os.chown(self.data, self.account.pw_uid, self.account.pw_gid)

    def file(self, path, mode=0o644):
        path.write_bytes(b"SYNTHETIC TEST FILE\n")
        path.chmod(mode)
        return path

    def run_cli(self, *extra, expected=0, as_root=False, user=None):
        command = [sys.executable, str(self.script), "--expected-user",
                   user or self.account.pw_name, "--read-only", str(self.code),
                   "--read-only", str(self.tools), "--writable", str(self.data), *map(str, extra)]
        identity = {} if as_root else {"user": self.account.pw_uid,
                                       "group": self.account.pw_gid, "extra_groups": []}
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                env={**os.environ, "PYTHONUTF8": "1"}, cwd=self.base,
                                timeout=20, **identity)
        self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        return json.loads(result.stdout) if result.stdout else None

    def assert_issue(self, report, code, path):
        self.assertIn((code, str(path)), [(item["code"], item["path"]) for item in report["issues"]])

    def test_protected_code_tools_and_private_writable_data_pass(self):
        report = self.run_cli()
        self.assertTrue(report["ok"])
        self.assertEqual(report["uid"], self.account.pw_uid)
        self.assertEqual(report["issues"], [])

    def test_group_writable_source_and_directory_fail(self):
        for path, mode in [(self.source, 0o660), (self.code, 0o770)]:
            with self.subTest(path=path):
                original = path.stat()
                os.chown(path, 0, self.account.pw_gid)
                path.chmod(mode)
                self.assert_issue(self.run_cli(expected=1), "writable", path)
                os.chown(path, original.st_uid, original.st_gid)
                path.chmod(stat.S_IMODE(original.st_mode))

    def test_world_writable_script_and_template_are_both_checked(self):
        for path in [self.tool, self.template]:
            with self.subTest(path=path):
                path.chmod(0o666)
                before = (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
                self.assert_issue(self.run_cli(expected=1), "writable", path)
                self.assertEqual(before, (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns))
                path.chmod(0o644)

    def test_runtime_owned_readonly_file_fails(self):
        os.chown(self.source, self.account.pw_uid, self.account.pw_gid)
        self.source.chmod(0o444)
        self.assert_issue(self.run_cli(expected=1), "owned_by_runtime", self.source)

    def test_writable_ancestor_allows_replacing_readonly_root(self):
        self.base.chmod(0o777)
        self.assert_issue(self.run_cli(expected=1), "replaceable_ancestor", self.base)

    def test_runtime_owned_ancestor_can_restore_its_write_permission(self):
        os.chown(self.base, self.account.pw_uid, self.account.pw_gid)
        self.base.chmod(0o555)
        self.assert_issue(self.run_cli(expected=1), "replaceable_ancestor", self.base)

    @unittest.skipUnless(shutil.which("setfacl"), "acl package required")
    def test_named_user_acl_write_is_detected(self):
        subprocess.run(["setfacl", "-m", f"u:{self.account.pw_uid}:rw", str(self.source)], check=True)
        self.assert_issue(self.run_cli(expected=1), "writable", self.source)

    def test_linked_dependency_tree_is_checked(self):
        dependency = self.base / "dependency"
        dependency.mkdir(mode=0o755)
        writable = self.file(dependency / "module.py", 0o666)
        (self.code / "dependency").symlink_to(dependency, target_is_directory=True)
        self.assert_issue(self.run_cli(expected=1), "writable", writable)

    def test_readonly_link_target_can_be_replaced_via_target_parent(self):
        dependency = self.base / "dependency"
        dependency.mkdir(mode=0o777)
        dependency.chmod(0o777)
        target = self.file(dependency / "module.py")
        (self.code / "module.py").symlink_to(target)
        self.assert_issue(self.run_cli(expected=1), "replaceable_ancestor", dependency)

    def test_venv_style_link_to_already_checked_directory_passes(self):
        library = self.code / "lib"
        library.mkdir(mode=0o755)
        self.file(library / "module.py")
        (self.code / "lib64").symlink_to(library, target_is_directory=True)
        self.assertTrue(self.run_cli()["ok"])

    def test_broken_and_unresolvable_links_fail(self):
        link = self.code / "link"
        for target in [self.base / "missing", link]:
            with self.subTest(target=target):
                link.symlink_to(target)
                self.assert_issue(self.run_cli(expected=1), "cannot_inspect", link)
                link.unlink()

    def test_unreadable_code_subdirectory_cannot_be_silently_skipped(self):
        hidden = self.code / "hidden"
        hidden.mkdir(mode=0o700)
        self.file(hidden / "module.py")
        self.assert_issue(self.run_cli(expected=1), "cannot_inspect", hidden)

    def test_nested_data_is_only_excluded_when_explicit(self):
        nested = self.code / "uploads"
        nested.mkdir(mode=0o700)
        os.chown(nested, self.account.pw_uid, self.account.pw_gid)
        payload = self.file(nested / "upload.txt", 0o600)
        os.chown(payload, self.account.pw_uid, self.account.pw_gid)
        self.run_cli(expected=1)
        before = (payload.read_bytes(), payload.stat().st_mode)
        self.assertTrue(self.run_cli("--writable", nested)["ok"])
        self.assertEqual(before, (payload.read_bytes(), payload.stat().st_mode))

    def test_entire_readonly_tree_cannot_be_excluded(self):
        self.run_cli("--writable", self.code, expected=2)
        self.run_cli("--writable", self.base, expected=2)

    def test_readonly_data_directory_fails(self):
        os.chown(self.data, 0, 0)
        self.data.chmod(0o755)
        self.assert_issue(self.run_cli(expected=1), "data_not_accessible", self.data)

    def test_wrong_identity_and_root_checks_are_rejected(self):
        self.run_cli(as_root=True, expected=2)
        self.run_cli(user="root", expected=2)

    def test_missing_path_cannot_pass(self):
        self.run_cli("--read-only", self.base / "missing", expected=2)


if __name__ == "__main__":
    unittest.main()
