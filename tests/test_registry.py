from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import registry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "server-deployments.md"

    def command(self, *arguments):
        command = [sys.executable, str(SCRIPTS / "registry.py"), "--registry", str(self.path)]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            command += ["--allow-root"]
        return [*command, *arguments]

    def run_cli(self, *arguments, ok=True):
        result = subprocess.run(self.command(*arguments), capture_output=True, text=True,
                                encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return result

    def upsert(self, project="translator", *arguments):
        return self.run_cli("upsert", "--project", project, "--server", "server-a",
                            "--user-url", "https://translate.example.com", "--user-port", "443",
                            "--backend-port", "18001", *arguments)

    def test_chinese_initialization_and_machine_read(self):
        self.run_cli("init")
        self.upsert()
        self.assertIn("服务器部署登记表", self.path.read_text(encoding="utf-8"))
        self.assertIn("项目,服务器", self.run_cli("list").stdout)
        rows = json.loads(self.run_cli("get", "--project", "translator").stdout)
        self.assertEqual(rows[0]["Backend Bind"], "127.0.0.1")

    def test_pipe_backslash_entities_chinese_round_trip(self):
        notes = r"中文 | C:\test\\file \| &amp; <tag> end\\"
        self.upsert("translator", "--notes", notes, "--security", "应用登录 | IP 限制")
        row = registry.load_registry(self.path).rows[0]
        self.assertEqual(row["Notes"], notes)
        self.assertEqual(row["Security"], "应用登录 | IP 限制")
        self.run_cli("upsert", "--project", "translator", "--server", "server-a",
                     "--notes", notes)
        self.assertEqual(registry.load_registry(self.path).rows[0]["Notes"], notes)

    def test_legacy_migration_backup_suffix_and_optional_fields(self):
        headers = registry.HEADERS[:-1]
        row = {h: "" for h in headers}
        row.update({"Project": "old", "Server": "server-a", "User Port": "443",
                    "Nginx Listen Port": "8443", "Backend Bind": "::1",
                    "Backend Port": "18009", "Process Manager": "docker-compose",
                    "Security": "app-login | IP allowlist", "Notes": "保留内容"})
        suffix = "\n\n## 运维说明\n不能删除\n\n| Operator | Value |\n|---|---|\n| Alice | 1 |\n"
        body = ("# Server Deployments Registry\n\n" + registry.format_row(headers) + "\n"
                + "|" + "|".join("---" for _ in headers) + "|\n"
                + registry.format_row([row[h] for h in headers]) + "\n" + suffix)
        self.path.write_text(body, encoding="utf-8")
        self.run_cli("upsert", "--project", "old", "--server", "server-a",
                     "--credential-refs", "old/prod/key")
        loaded = registry.load_registry(self.path)
        self.assertEqual(len(loaded.rows), 1)
        for key in ("Nginx Listen Port", "Backend Bind", "Process Manager", "Security", "Notes"):
            self.assertEqual(loaded.rows[0][key], row[key])
        self.assertTrue(self.path.read_text(encoding="utf-8").endswith(suffix))
        backups = list(self.path.parent.glob("*.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), body)

    def test_old_escaped_pipe_is_one_cell(self):
        cells = registry.split_row(r"| one | app-login \| IP | tail |")
        self.assertEqual(cells, ["one", "app-login | IP", "tail"])

    def test_pre_security_schema_is_accepted(self):
        headers = registry.LEGACY_HEADERS
        cells = ["demo" if h == "Project" else "server-a" if h == "Server" else "-"
                 for h in headers]
        self.path.write_text(registry.format_row(headers) + "\n"
                             + "|" + "|".join("---" for _ in headers) + "|\n"
                             + registry.format_row(cells) + "\n", encoding="utf-8")
        self.run_cli("upsert", "--project", "demo", "--server", "server-a", "--security", "未公开")
        self.assertEqual(registry.load_registry(self.path).rows[0]["Security"], "未公开")

    def test_unknown_or_malformed_table_remains_unchanged(self):
        for content in ("unknown user document\n", registry.template_registry_text().replace(
                "|---|---|---|---:", "|---|---:")):
            self.path.write_text(content, encoding="utf-8")
            self.run_cli("upsert", "--project", "demo", "--server", "server-a", ok=False)
            self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_explicit_empty_clears_optional_field(self):
        self.upsert("translator", "--notes", "清空前")
        self.run_cli("upsert", "--project", "translator", "--server", "server-a", "--notes", "")
        self.assertEqual(registry.load_registry(self.path).rows[0]["Notes"], "")

    def test_force_init_backs_up(self):
        self.upsert()
        before = self.path.read_bytes()
        self.run_cli("init", "--force")
        self.assertEqual(next(self.path.parent.glob("*.bak.*")).read_bytes(), before)
        self.assertEqual(registry.load_registry(self.path).rows, [])

    def test_find_free_scopes_server_and_excludes_live_ports(self):
        self.upsert()
        result = self.run_cli("find-free", "--server", "server-b", "--start", "18001",
                              "--end", "18003", "--used", "18002")
        self.assertEqual(result.stdout.strip(), "18001")
        result = self.run_cli("find-free", "--server", "server-a", "--start", "18001",
                              "--end", "18003", "--used", "18002")
        self.assertEqual(result.stdout.strip(), "18003")

    def test_invalid_ports_and_ranges_fail(self):
        for arguments in (("find-free", "--start", "0", "--end", "80"),
                          ("find-free", "--start", "80", "--end", "70000"),
                          ("find-free", "--start", "90", "--end", "80")):
            self.run_cli(*arguments, ok=False)

    def test_allow_root_requires_explicit_path(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "registry.py"),
                                 "--allow-root", "init"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_parallel_upserts_preserve_all_projects(self):
        processes = [subprocess.Popen(self.command(
            "upsert", "--project", f"project-{i}", "--server", "server-a",
            "--user-url", f"https://p{i}.example.com", "--user-port", "443",
            "--backend-port", str(18001 + i)), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for i in range(8)]
        for process in processes:
            out, err = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, err.decode("utf-8"))
        self.assertEqual(len(registry.load_registry(self.path).rows), 8)


if __name__ == "__main__":
    unittest.main()
