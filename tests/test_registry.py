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
        return self.run_cli("upsert", "--project", project,
                            "--user-url", "https://translate.example.com", "--user-port", "443",
                            "--backend-port", "18001", *arguments)

    def test_chinese_initialization_and_machine_read(self):
        self.run_cli("init")
        self.upsert()
        self.assertIn("服务器部署登记表", self.path.read_text(encoding="utf-8"))
        self.assertIn("项目,已部署版本", self.run_cli("list").stdout)
        rows = json.loads(self.run_cli("get", "--project", "translator").stdout)
        self.assertNotIn("Server", rows[0])
        self.assertEqual(rows[0]["Deployed Version"], "")
        self.assertEqual(rows[0]["Backend Bind"], "127.0.0.1")
        self.assertEqual(rows[0]["Nginx Listen Port"], "443")

    def test_non_nginx_entry_keeps_real_ports_and_survives_partial_update(self):
        self.upsert("translator", "--no-nginx", "--notes", "入口为 Caddy；已验证 HTTPS")
        row = json.loads(self.run_cli("get", "--project", "translator").stdout)[0]
        self.assertEqual(row["Nginx Listen Port"], "")
        self.assertEqual(row["Nginx Config"], "")
        self.assertEqual(row["User Port"], "443")
        self.assertEqual(row["Backend Port"], "18001")
        self.run_cli("upsert", "--project", "translator",
                     "--credential-refs", "translator/prod/key")
        updated = registry.load_registry(self.path).rows[0]
        self.assertEqual(updated["Nginx Listen Port"], "")
        self.assertEqual(updated["Nginx Config"], "")
        self.assertEqual(updated["Notes"], row["Notes"])
        self.assertEqual(updated["Credential Refs"], "translator/prod/key")

    def test_worker_registration_version_and_ports(self):
        self.run_cli("upsert", "--project", "report", "--no-ingress",
                     "--deployed-version", "v1.2.0", "--unit", "report.timer / report.service",
                     "--health-check", "核对输出文件的日期")
        self.run_cli("upsert", "--project", "REPORT", "--notes", "数据在 data/")
        row, = json.loads(self.run_cli("list", "--json").stdout)
        for field in registry.NETWORK_FIELDS:
            self.assertEqual(row[field], "", field)
        self.assertEqual(row["Deployed Version"], "v1.2.0")
        self.assertEqual(row["Unit/Process"], "report.timer / report.service")
        self.assertEqual(row["Health Check"], "核对输出文件的日期")
        self.assertEqual(self.run_cli("find-free", "--start", "18001", "--end", "18001").stdout.strip(), "18001")
        self.run_cli("upsert", "--project", "report", "--deployed-version", "")
        self.assertEqual(registry.load_registry(self.path).rows[0]["Deployed Version"], "")

    def test_switch_between_web_and_background_without_stale_endpoints(self):
        self.upsert("report", "--deployed-version", "v1", "--nginx-config", "/etc/nginx/report.conf")
        self.run_cli("upsert", "--project", "report", "--no-ingress")
        row = registry.load_registry(self.path).rows[0]
        self.assertTrue(all(not row[key] for key in registry.NETWORK_FIELDS))
        before = self.path.read_bytes()
        partial = self.run_cli("upsert", "--project", "report", "--user-port", "443", ok=False)
        self.assertIn("--backend-port", partial.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        self.upsert("report")
        row = registry.load_registry(self.path).rows[0]
        self.assertEqual((row["Backend Bind"], row["Nginx Listen Port"], row["Backend Port"]),
                         ("127.0.0.1", "443", "18001"))
        self.assertEqual(row["Deployed Version"], "v1")

    def test_no_ingress_rejects_web_options_without_writing(self):
        self.upsert()
        before = self.path.read_bytes()
        for args in (("--user-url", "https://example.org"), ("--user-port", "443"),
                     ("--backend-bind", "127.0.0.1"), ("--backend-port", "18001"),
                     ("--nginx-port", "443"), ("--nginx-config", "/etc/nginx/test.conf"),
                     ("--no-nginx",)):
            with self.subTest(args=args):
                self.run_cli("upsert", "--project", "translator", "--no-ingress", *args, ok=False)
                self.assertEqual(self.path.read_bytes(), before)

    def test_switch_to_non_nginx_clears_stale_config_and_preserves_service(self):
        self.upsert("translator", "--nginx-port", "8443",
                    "--nginx-config", "/etc/nginx/conf.d/translator.conf",
                    "--unit", "translator.service", "--app-dir", "/srv/translator/current",
                    "--credential-refs", "translator/prod/key", "--security", "HTTPS；应用登录")
        before = registry.load_registry(self.path).rows[0]
        self.run_cli("upsert", "--project", "translator",
                     "--no-nginx", "--notes", "现有入口为 Caddy")
        after = registry.load_registry(self.path).rows[0]
        for key in registry.HEADERS:
            if key not in ("Nginx Listen Port", "Nginx Config", "Notes", "Updated"):
                self.assertEqual(after[key], before[key])
        self.assertEqual(after["Nginx Listen Port"], "")
        self.assertEqual(after["Nginx Config"], "")
        self.assertEqual(after["Notes"], "现有入口为 Caddy")

    def test_conflicting_gateway_arguments_do_not_change_registry(self):
        self.upsert()
        before = self.path.read_bytes()
        for option, value in (("--nginx-port", "443"),
                              ("--nginx-config", "/etc/nginx/conf.d/translator.conf")):
            with self.subTest(option=option):
                self.run_cli("upsert", "--project", "translator",
                             "--no-nginx", option, value, ok=False)
                self.assertEqual(self.path.read_bytes(), before)

    def test_pipe_backslash_entities_chinese_round_trip(self):
        notes = r"中文 | C:\test\\file \| &amp; <tag> end\\"
        self.upsert("translator", "--notes", notes, "--security", "应用登录 | IP 限制")
        row = registry.load_registry(self.path).rows[0]
        self.assertEqual(row["Notes"], notes)
        self.assertEqual(row["Security"], "应用登录 | IP 限制")
        self.run_cli("upsert", "--project", "translator",
                     "--notes", notes)
        self.assertEqual(registry.load_registry(self.path).rows[0]["Notes"], notes)

    def test_legacy_migration_backup_suffix_and_optional_fields(self):
        headers = registry.PREVIOUS_HEADERS[:-1]
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
        self.run_cli("upsert", "--project", "old",
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

    def write_old_chinese_table(self, second_project):
        headers = ["项目", "服务器", "访问地址", "用户端口", "Nginx 监听端口", "后端绑定地址",
                   "后端端口", "进程管理器", "服务单元/进程", "应用目录", "健康检查",
                   "Nginx 配置", "安全措施", "更新日期", "备注", "凭据引用"]
        rows = [
            ["files", "192.0.2.1", "https://files.example.org", "443", "443", "127.0.0.1",
             "18001", "systemd", "files.service", "/srv/files", "/health", "/etc/nginx/files.conf",
             "TLS", "2025-01-01", "历史版本 v1", "files/prod/key"],
            [second_project, "server", "", "", "", "", "", "systemd", "report.timer",
             "/srv/report", "核对当日报表", "", "", "", "本机数据", ""],
        ]
        body = ("# 运维手记\n\n" + registry.format_row(headers) + "\n"
                + registry.separator_row() + "\n"
                + "".join(registry.format_row(row) + "\n" for row in rows)
                + "\n## 自定义备注\nSSH 别名曾经更换。\n")
        self.path.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))

    def test_old_host_aliases_migrate_once_without_guessing_the_version(self):
        self.write_old_chinese_table("report")
        original = self.path.read_bytes()
        self.assertEqual(json.loads(self.run_cli("get", "--project", "files").stdout)[0]["Server"], "192.0.2.1")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.bak.*")), [])
        self.run_cli("upsert", "--project", "report", "--server", "server", "--deployed-version", "v2")
        rows = {row["Project"]: row for row in registry.load_registry(self.path).rows}
        self.assertEqual(set(rows), {"files", "report"})
        self.assertNotIn("Server", rows["files"])
        self.assertEqual((rows["files"]["Deployed Version"], rows["files"]["Notes"]), ("", "历史版本 v1"))
        self.assertEqual((rows["files"]["Backend Port"], rows["files"]["Credential Refs"]),
                         ("18001", "files/prod/key"))
        self.assertEqual((rows["report"]["Unit/Process"], rows["report"]["Deployed Version"]),
                         ("report.timer", "v2"))
        data = self.path.read_bytes()
        self.assertTrue(data.startswith("# 运维手记\r\n\r\n".encode("utf-8")))
        self.assertTrue(data.endswith("## 自定义备注\r\nSSH 别名曾经更换。\r\n".encode("utf-8")))
        self.run_cli("upsert", "--project", "report", "--no-ingress")
        backup, = self.path.parent.glob("*.bak.*")
        self.assertEqual(backup.read_bytes(), original)

    def test_ambiguous_legacy_projects_remain_readable_and_cannot_be_merged_by_upsert(self):
        self.write_old_chinese_table("FILES")
        original = self.path.read_bytes()
        rows = json.loads(self.run_cli("get", "--project", "files").stdout)
        self.assertEqual(len(rows), 2)
        selected, = json.loads(self.run_cli("get", "--project", "files", "--server", "server").stdout)
        self.assertEqual(selected["Unit/Process"], "report.timer")
        failed = self.run_cli("upsert", "--project", "third", "--no-ingress", ok=False)
        self.assertIn("同名项目", failed.stderr)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.bak.*")), [])

    def test_migration_updates_only_unmodified_template_guidance(self):
        self.write_old_chinese_table("report")
        original = self.path.read_bytes().decode("utf-8").partition("## 自定义备注")[0]
        stock = registry.OLD_TEMPLATE_GUIDANCE
        operator_notes = "## 运维备注\n" + stock + "\n"  # The same text can be part of an operator's own notes.
        for customized in (False, True):
            with self.subTest(customized=customized):
                guidance = stock + ("\n本机例外" if customized else "")
                self.path.write_bytes((original + guidance + "\n\n" + operator_notes).encode("utf-8"))
                self.run_cli("upsert", "--project", "report", "--no-ingress")
                suffix = registry.load_registry(self.path).suffix
                self.assertTrue(suffix.endswith(operator_notes))
                before_notes = suffix.partition("## 运维备注")[0]
                if customized:
                    self.assertEqual(before_notes.strip(), guidance)
                else:
                    self.assertIn("--no-ingress", before_notes)
                    self.assertNotIn("备注记录实际版本", before_notes)

    def test_pre_security_schema_is_accepted(self):
        headers = registry.LEGACY_HEADERS
        cells = ["demo" if h == "Project" else "server-a" if h == "Server" else "-"
                 for h in headers]
        self.path.write_text(registry.format_row(headers) + "\n"
                             + "|" + "|".join("---" for _ in headers) + "|\n"
                             + registry.format_row(cells) + "\n", encoding="utf-8")
        self.run_cli("upsert", "--project", "demo", "--security", "未公开")
        self.assertEqual(registry.load_registry(self.path).rows[0]["Security"], "未公开")

    def test_unknown_or_malformed_table_remains_unchanged(self):
        for content in ("unknown user document\n", registry.template_registry_text().replace(
                "|---|---|---|---:", "|---|---:")):
            self.path.write_text(content, encoding="utf-8")
            self.run_cli("upsert", "--project", "demo", ok=False)
            self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_explicit_empty_clears_optional_field(self):
        self.upsert("translator", "--notes", "清空前")
        self.run_cli("upsert", "--project", "translator", "--notes", "")
        self.assertEqual(registry.load_registry(self.path).rows[0]["Notes"], "")

    def test_identifiers_are_normalized_and_invalid_required_values_do_not_write(self):
        self.upsert()
        self.run_cli("upsert", "--project", " translator ", "--server", " server-a ",
                     "--notes", "更新同一条记录")
        rows = json.loads(self.run_cli("get", "--project", " translator ",
                                      "--server", " server-a ").stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Notes"], "更新同一条记录")
        before = self.path.read_bytes()
        for option in ("--project", "--server", "--user-url"):
            for value in ("", "   ", "-", "first\nsecond", "first\u2028second", "first\tsecond"):
                with self.subTest(option=option, value=value):
                    self.run_cli("upsert", "--project", "translator",
                                 option, value, ok=False)
                    self.assertEqual(self.path.read_bytes(), before)

    def test_missing_identifiers_in_existing_rows_are_not_silently_discarded(self):
        for missing in ("Project", "Server"):
            with self.subTest(missing=missing):
                row = {h: "" for h in registry.PREVIOUS_HEADERS}
                row.update({"Project": "translator", "Server": "server-a", "Notes": "应保留的记录"})
                row[missing] = ""
                body = (registry.format_row(["项目", "服务器", *registry.ZH_HEADERS[2:]]) + "\n"
                        + registry.separator_row() + "\n"
                        + registry.format_row([row[h] for h in registry.PREVIOUS_HEADERS]) + "\n")
                self.path.write_text(body, encoding="utf-8")
                before = self.path.read_bytes()
                self.run_cli("upsert", "--project", "translator",
                             "--notes", "修改", ok=False)
                self.assertEqual(self.path.read_bytes(), before)

    def test_unicode_line_separators_in_notes_keep_table_readable(self):
        lines = ["first", "second", "third", "fourth", "fifth"]
        self.upsert("translator", "--notes", "first\u2028second\u2029third\x85fourth\v fifth")
        row = registry.load_registry(self.path).rows[0]
        self.assertEqual(row["Notes"].split(), lines)
        self.run_cli("upsert", "--project", "translator", "--security", "未公开")
        self.assertEqual(registry.load_registry(self.path).rows[0]["Notes"], row["Notes"])

    @unittest.skipUnless(os.name == "posix", "Linux symlinks")
    def test_force_init_rejects_symlink_without_creating_backup(self):
        target = self.path.parent / "unrelated.txt"
        target.write_bytes(b"SYNTHETIC-UNRELATED-CONTENT")
        self.path.symlink_to(target)
        self.run_cli("init", "--force", ok=False)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_bytes(), b"SYNTHETIC-UNRELATED-CONTENT")
        self.assertEqual(list(self.path.parent.glob("*.bak.*")), [])

    def test_force_init_backs_up(self):
        self.upsert()
        before = self.path.read_bytes()
        self.run_cli("init", "--force")
        self.assertEqual(next(self.path.parent.glob("*.bak.*")).read_bytes(), before)
        self.assertEqual(registry.load_registry(self.path).rows, [])

    def test_find_free_counts_all_local_and_live_ports_even_with_old_alias(self):
        self.upsert()
        result = self.run_cli("find-free", "--server", "server-b", "--start", "18001",
                              "--end", "18003", "--used", "18002")
        self.assertEqual(result.stdout.strip(), "18003")
        result = self.run_cli("find-free", "--start", "18001",
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
            "upsert", "--project", f"project-{i}",
            "--user-url", f"https://p{i}.example.com", "--user-port", "443",
            "--backend-port", str(18001 + i)), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for i in range(8)]
        for process in processes:
            out, err = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, err.decode("utf-8"))
        self.assertEqual(len(registry.load_registry(self.path).rows), 8)


if __name__ == "__main__":
    unittest.main()
