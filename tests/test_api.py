import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.convert import create_app, run


class ConvertApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(port=8000))

    def test_index_returns_html(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("PDF → DOCX OCR", response.text)
        self.assertIn("form", response.text)

    def test_info_endpoint(self) -> None:
        response = self.client.get("/info")
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertTrue(json_data["ok"])
        self.assertEqual(json_data["upload_endpoint"], "POST /convert")
        self.assertIn("curl", json_data)

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_run_raises_clear_error_when_command_missing(self) -> None:
        with patch("app.convert.subprocess.run", side_effect=FileNotFoundError("No such file or directory")):
            with self.assertRaises(RuntimeError) as cm:
                run(["missing-cmd"])

        self.assertIn("Command not found: missing-cmd", str(cm.exception))
