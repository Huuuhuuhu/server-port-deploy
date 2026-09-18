from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import credentials
from safe_io import file_lock

AGE_AVAILABLE = bool(shutil.which("age") and shutil.which("age-keygen"))
if os.environ.get("SPD_REQUIRE_AGE") == "1" and not AGE_AVAILABLE:
    raise RuntimeError("Real age is required for this test run")


@unittest.skipUnless(AGE_AVAILABLE, "install age and age-keygen to run encryption integration tests")
class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "store"
        self.identity = self.base / "keys" / "identity.txt"
        self.secret = " ONLY-TEST-千问:AbC-mF6|\\'\"\n"
        self.run_cli("init")

    def command(self, *args):
        return [sys.executable, str(SCRIPTS / "credentials.py"),
                "--store", str(self.root), "--identity", str(self.identity), *args]

    def run_cli(self, *args, data=None, ok=True):
        result = subprocess.run(self.command(*args), input=data, capture_output=True,
                                env={**os.environ, "PYTHONUTF8": "1"}, timeout=40)
        self.assertEqual(result.returncode == 0, ok, result.stderr.decode("utf-8", "replace"))
        self.assertNotIn(self.secret.encode(), result.stdout + result.stderr)
        return result

    def put(self, name="dashscope-api-key", project="translator", value=None, *extra):
        return self.run_cli("put", "--project", project, "--environment", "prod",
                            "--name", name, "--stdin", "--purpose", "中文测试用途",
                            *extra, data=(value if value is not None else self.secret).encode("utf-8"))

    def execute_check(self, value, *extra):
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        code = ("import hashlib,os; "
                f"assert hashlib.sha256(os.environ['DASHSCOPE_API_KEY'].encode()).hexdigest()=={digest!r}; "
                "assert 'OTHER_PROJECT_KEY' not in os.environ; print('verified')")
        return self.run_cli("exec", "--project", "translator", "--environment", "prod",
                            "--bind", "DASHSCOPE_API_KEY=dashscope-api-key",
                            *extra, "--", sys.executable, "-c", code)

    def test_utf8_exact_bytes_and_metadata_only(self):
        self.put()
        self.put("unused-key", value="OTHER-ONLY-TEST")
        self.execute_check(self.secret)
        result = self.run_cli("list", "--project", "translator", "--environment", "prod")
        records = json.loads(result.stdout)
        self.assertEqual(records[0]["reference"], "translator/prod/dashscope-api-key")
        self.assertNotIn("value", records[0])
        cipher = (self.root / "translator/prod.age").read_bytes()
        self.assertNotIn(self.secret.encode(), cipher)
        self.assertTrue(cipher.startswith(b"age-encryption.org/"))
        self.assertNotIn(self.secret.encode(), self.identity.read_bytes())

    def test_init_is_idempotent_and_preserves_identity(self):
        original = self.identity.read_bytes()
        self.run_cli("init")
        self.assertEqual(original, self.identity.read_bytes())

    def test_replacement_explicit_and_preserves_metadata(self):
        self.put()
        args = ("put", "--project", "translator", "--environment", "prod",
                "--name", "dashscope-api-key", "--stdin")
        self.run_cli(*args, data=b"replacement-only-test", ok=False)
        self.execute_check(self.secret)
        self.run_cli(*args, "--replace", data=b"replacement-only-test")
        self.execute_check("replacement-only-test")
        rows = json.loads(self.run_cli("list", "--project", "translator", "--environment", "prod").stdout)
        self.assertEqual(rows[0]["purpose"], "中文测试用途")

    def test_missing_reference_does_not_launch(self):
        self.put()
        marker = self.base / "launched"
        result = self.run_cli("exec", "--project", "translator", "--environment", "prod",
                             "--bind", "DASHSCOPE_API_KEY=missing", "--",
                             sys.executable, "-c", f"open({str(marker)!r},'w').close()", ok=False)
        self.assertFalse(marker.exists())
        self.assertEqual(result.stdout, b"")

    def test_damaged_ciphertext_fails_closed(self):
        self.put()
        path = self.root / "translator/prod.age"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        result = self.run_cli("check", "--project", "translator", "--environment", "prod",
                             "--name", "dashscope-api-key", ok=False)
        self.assertNotIn(b"AGE-SECRET", result.stderr)
        self.assertEqual(path.read_bytes(), bytes(data))

    def test_missing_identity_does_not_regenerate(self):
        self.put()
        self.identity.unlink()
        self.run_cli("init", ok=False)
        self.assertFalse(self.identity.exists())

    def test_wrong_identity_does_not_overwrite_cipher(self):
        self.put()
        before = (self.root / "translator/prod.age").read_bytes()
        other = credentials.Vault(self.base / "other-store", self.base / "other-keys/identity.txt")
        other.prepare()
        with file_lock(other.root / ".lock"):
            other.initialize()
        self.identity.write_bytes(other.identity.read_bytes())
        self.run_cli("put", "--project", "translator", "--environment", "prod",
                     "--name", "another", "--stdin", data=b"dummy-only-test", ok=False)
        self.assertEqual(before, (self.root / "translator/prod.age").read_bytes())

    def test_scope_swap_detected(self):
        self.put()
        self.put("key", "another-project")
        source = self.root / "translator/prod.age"
        target = self.root / "another-project/prod.age"
        target.write_bytes(source.read_bytes())
        self.run_cli("list", "--project", "another-project", "--environment", "prod", ok=False)

    def test_check_does_not_claim_provider_validity(self):
        self.put()
        self.run_cli("check", "--project", "translator", "--environment", "prod",
                     "--name", "dashscope-api-key")
        self.run_cli("check", "--project", "translator", "--environment", "staging",
                     "--name", "dashscope-api-key", ok=False)

    def test_vault_owner_can_read_other_scope_despite_selected_bindings(self):
        self.put()
        other_value = "ONLY-TEST-OTHER-PROJECT"
        self.put("other-key", "another-project", other_value)
        digest = hashlib.sha256(other_value.encode()).hexdigest()
        # Selection is deliberately not an ACL: an owner process can open another scope.
        code = (
            "import hashlib,os,sys; from pathlib import Path; "
            f"sys.path.insert(0,{str(SCRIPTS)!r}); from credentials import Vault; "
            f"vault=Vault(Path({str(self.root)!r}),Path({str(self.identity)!r})); "
            "assert 'OTHER_PROJECT_KEY' not in os.environ; "
            "other=vault.read('another-project','prod')['records']['other-key']['value']; "
            f"assert hashlib.sha256(other.encode()).hexdigest()=={digest!r}; "
            "print('owner access verified without disclosing values')"
        )
        self.run_cli("exec", "--project", "translator", "--environment", "prod",
                     "--bind", "DASHSCOPE_API_KEY=dashscope-api-key",
                     "--", sys.executable, "-c", code)

    def test_unsafe_binding_names_and_duplicates_rejected(self):
        self.put()
        for binding in ("PATH=dashscope-api-key", "LD_PRELOAD=dashscope-api-key",
                        "bad;name=dashscope-api-key", "DASHSCOPE_API_KEY=../key"):
            self.run_cli("exec", "--project", "translator", "--environment", "prod",
                         "--bind", binding, "--", sys.executable, "-c", "raise SystemExit(99)", ok=False)
        self.run_cli("exec", "--project", "translator", "--environment", "prod",
                     "--bind", "KEY=dashscope-api-key", "--bind", "KEY=dashscope-api-key",
                     "--", sys.executable, "-c", "raise SystemExit(99)", ok=False)

    def test_input_validation_leaves_store_unchanged(self):
        for raw in (b"", b"bad\0value", b"\xff", b"x" * (credentials.MAX_VALUE + 1)):
            self.run_cli("put", "--project", "translator", "--environment", "prod",
                         "--name", "key", "--stdin", data=raw, ok=False)
        self.assertFalse((self.root / "translator/prod.age").exists())
        self.run_cli("put", "--project", "../outside", "--environment", "prod",
                     "--name", "key", "--stdin", data=b"dummy-only-test", ok=False)

    def test_chinese_catalog_excludes_values_and_protected_destinations(self):
        self.put()
        output = self.base / "server-credentials.md"
        self.run_cli("catalog", "--out", str(output))
        text = output.read_text(encoding="utf-8")
        self.assertIn("服务器凭据目录", text)
        self.assertIn("translator/prod/dashscope-api-key", text)
        self.assertNotIn(self.secret, text)
        original = self.identity.read_bytes()
        self.run_cli("catalog", "--out", str(self.identity), ok=False)
        self.run_cli("catalog", "--out", str(self.root / "translator/prod.age"), ok=False)
        self.assertEqual(self.identity.read_bytes(), original)

    def test_backup_restore_with_separate_identity(self):
        self.put()
        destination = self.base / "backup"
        self.run_cli("backup", "--out", str(destination))
        self.assertFalse((destination / "identity.txt").exists())
        files = [p.relative_to(destination).as_posix()
                 for p in destination.rglob("*") if p.is_file()]
        self.assertEqual(files, ["translator/prod.age"])
        restored = credentials.Vault(destination, self.identity)
        restored.prepare()
        self.assertEqual(restored.read("translator", "prod")["records"]["dashscope-api-key"]["value"],
                         self.secret)
        self.run_cli("backup", "--out", str(destination), ok=False)
        self.run_cli("backup", "--out", str(self.root / "backup"), ok=False)

    def test_identity_cannot_live_in_cipher_store(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "credentials.py"),
                                 "--store", str(self.root),
                                 "--identity", str(self.root / "identity.txt"), "init"],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_dot_paths_cannot_alias_protected_destinations(self):
        self.put()
        before = self.identity.read_bytes()
        alias = self.base / "store/../keys/identity.txt"
        self.run_cli("catalog", "--out", str(alias), ok=False)
        self.run_cli("backup", "--out", str(self.base / "keys/backup"), ok=False)
        self.assertEqual(before, self.identity.read_bytes())

    @unittest.skipUnless(os.name == "posix", "Linux symlinks")
    def test_catalog_cannot_follow_parent_symlink_to_identity(self):
        self.put()
        alias = self.base / "linked-keys"
        alias.symlink_to(self.identity.parent, target_is_directory=True)
        before = self.identity.read_bytes()
        self.run_cli("catalog", "--out", str(alias / "identity.txt"), ok=False)
        self.assertEqual(before, self.identity.read_bytes())

    def test_parallel_imports_preserve_all_records(self):
        processes = [subprocess.Popen(self.command(
            "put", "--project", "translator", "--environment", "prod",
            "--name", f"key-{i}", "--stdin"), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for i in range(8)]
        for process in processes:
            self.addCleanup(self.finish_process, process)
        for i, process in enumerate(processes):
            stdout, stderr = process.communicate(f"parallel-only-test-{i}".encode(), timeout=40)
            self.assertEqual(process.returncode, 0, stderr.decode("utf-8", "replace"))
        rows = json.loads(self.run_cli("list", "--project", "translator", "--environment", "prod").stdout)
        self.assertEqual(len(rows), 8)

    @staticmethod
    def finish_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate()

    @unittest.skipUnless(os.name == "posix", "Linux filesystem permissions")
    def test_permissions_checked_and_symlinks_rejected(self):
        self.put()
        self.assertEqual(self.identity.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        cipher = self.root / "translator/prod.age"
        self.assertEqual(cipher.stat().st_mode & 0o777, 0o600)
        self.identity.chmod(0o644)
        self.run_cli("list", "--project", "translator", "--environment", "prod", ok=False)
        self.identity.chmod(0o600)
        copy = self.base / "cipher-copy"
        copy.write_bytes(cipher.read_bytes())
        cipher.unlink()
        cipher.symlink_to(copy)
        self.run_cli("list", "--project", "translator", "--environment", "prod", ok=False)

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "run as root to check privilege drop")
    def test_root_launcher_drops_uid_gid_and_hides_vault(self):
        import pwd
        try:
            account = pwd.getpwnam("nobody")
        except KeyError:
            self.skipTest("no nobody account available")
        self.put()
        self.put("other-key", "another-project", "ONLY-TEST-OTHER-PROJECT")
        # Let the app traverse the fixture parent so the vault/key permissions are tested.
        self.base.chmod(0o755)
        groups = os.getgrouplist(account.pw_name, account.pw_gid)
        protected = [str(self.identity), str(self.root / "translator/prod.age"),
                     str(self.root / "another-project/prod.age")]
        code = f"""
import os, hashlib
assert os.getresuid() == ({account.pw_uid},) * 3
assert os.getresgid() == ({account.pw_gid},) * 3
assert set(os.getgroups()) == set({groups!r})
assert os.access({str(self.base)!r}, os.X_OK)
assert not os.access({str(self.root)!r}, os.R_OK)
for path in {protected!r}:
    try:
        with open(path, 'rb'):
            pass
    except PermissionError:
        continue
    raise AssertionError('application unexpectedly opened a protected credential file')
assert hashlib.sha256(os.environ['DASHSCOPE_API_KEY'].encode()).hexdigest() == {hashlib.sha256(self.secret.encode()).hexdigest()!r}
assert 'OTHER_PROJECT_KEY' not in os.environ
print('privileges verified')
"""
        self.run_cli("exec", "--project", "translator", "--environment", "prod",
                     "--bind", "DASHSCOPE_API_KEY=dashscope-api-key",
                     "--as-user", account.pw_name, "--", sys.executable, "-c", code)


if __name__ == "__main__":
    unittest.main()
