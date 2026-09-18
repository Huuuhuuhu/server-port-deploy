from __future__ import annotations
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import render_nginx

if os.environ.get("SPD_REQUIRE_NGINX") == "1" and not shutil.which("nginx"):
    raise RuntimeError("Real Nginx is required for this test run")


class NginxTests(unittest.TestCase):
    def generate(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / "render_nginx.py"),
                                 "--project", "translator", "--backend-port", "18001", *args],
                                capture_output=True, text=True, encoding="utf-8",
                                env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return result.stdout

    def test_preserves_external_host_port_and_tls_redirect_port(self):
        text = self.generate("--https", "--server-name", "translate.example.com",
                             "--public-port", "8443", "--redirect-http")
        self.assertIn("proxy_set_header Host $http_host;", text)
        self.assertIn("return 301 https://translate.example.com:8443$request_uri;", text)

    def test_streaming_does_not_enable_websocket_upgrade(self):
        text = self.generate("--streaming")
        self.assertIn("proxy_buffering off;", text)
        self.assertNotIn("proxy_set_header Upgrade", text)
        self.assertIn('proxy_set_header Connection "";', text)

    def test_websocket_and_ipv6_backend(self):
        text = self.generate("--websocket", "--backend-bind", "::1")
        self.assertIn("proxy_pass http://[::1]:18001;", text)
        self.assertIn("map $http_upgrade", text)
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", text)

    def test_invalid_inputs_rejected(self):
        cases = [
            ("--public-port", "70000"), ("--backend-port", "0"),
            ("--server-name", "good.example;\ninclude evil;"),
            ("--backend-bind", "localhost;"), ("--timeout", "-1"),
            ("--backend-bind", "fe80::1%eth0"),
            ("--backend-bind", "fe80::1%lo;\nadd_header X-Test injected;"),
            ("--redirect-http",), ("--https", "--redirect-http"),
            ("--https", "--server-name", "a.example", "--public-port", "80", "--redirect-http"),
            ("--https", "--ssl-certificate", "/tmp/x;bad", "--ssl-certificate-key", "/tmp/key"),
        ]
        for case in cases:
            with self.subTest(case=case):
                self.generate(*case, ok=False)

    def test_invalid_backend_cannot_replace_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "nginx.conf"
            output.write_bytes(b"existing config\n")
            self.generate("--backend-bind", "fe80::1%lo;\ninvalid;",
                          "--out", str(output), ok=False)
            self.assertEqual(output.read_bytes(), b"existing config\n")

    @unittest.skipUnless(shutil.which("nginx") and shutil.which("openssl"),
                         "nginx and openssl required for real configuration validation")
    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0,
                         "root required to validate the generated port 80 listener")
    def test_generated_configs_pass_real_nginx(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cert, key = base / "cert.pem", base / "key.pem"
            result = subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(cert), "-days", "1",
                "-subj", "/CN=translate.example.com",
            ], capture_output=True)
            self.assertEqual(result.returncode, 0)
            for extra in ([], ["--streaming"], ["--websocket"], [
                "--https", "--server-name", "translate.example.com", "--public-port", "8443",
                "--redirect-http", "--ssl-certificate", str(cert), "--ssl-certificate-key", str(key)
            ]):
                with self.subTest(extra=extra):
                    args = render_nginx.parse_args(["--project", "translator",
                                                    "--backend-port", "18001", *extra])
                    config = base / "nginx.conf"
                    config.write_text(
                        f"pid {base}/nginx.pid;\nerror_log stderr;\nevents {{}}\n"
                        "http {\naccess_log off;\n"
                        + "".join(f"{kind}_temp_path {base}/{kind};\n" for kind in
                                  ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
                        + render_nginx.render(args) + "\n}\n",
                        encoding="utf-8")
                    result = subprocess.run(["nginx", "-t", "-e", "stderr", "-p", str(base), "-c", str(config)],
                                            capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr.decode())


if __name__ == "__main__":
    unittest.main()
