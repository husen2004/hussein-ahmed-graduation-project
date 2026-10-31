from __future__ import annotations

import json
import secrets
import sqlite3
import io
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from database import init_db  # noqa: E402
import server  # noqa: E402
from tools.set_admin_password import password_hash


class HusseinAhmedProjectIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db_path = init_db.DB_PATH
        cls.original_server_db_path = server.DB_PATH
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="hussein-ahmed-tests-")
        cls.database_path = str(Path(cls.temp_dir.name) / "students.db")
        init_db.DB_PATH = cls.database_path
        server.DB_PATH = cls.database_path
        cls.httpd = server.HTTPServer(("127.0.0.1", 0), server.APIHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        init_db.DB_PATH = cls.original_db_path
        server.DB_PATH = cls.original_server_db_path
        cls.temp_dir.cleanup()

    def setUp(self):
        path = Path(self.database_path)
        if path.exists():
            path.unlink()
        init_db.init_database()
        self.test_password = secrets.token_urlsafe(24)
        with sqlite3.connect(self.database_path) as conn:
            conn.execute("UPDATE Users SET PasswordHash=? WHERE Username='admin'", (password_hash(self.test_password),))
        status, body, _ = self.request(
            "/api/auth/login",
            method="POST",
            data={"Username": "admin", "Password": self.test_password},
            authenticated=False,
        )
        self.assertEqual(status, 200)
        self.token = body["token"]

    def request(self, path, method="GET", data=None, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated and hasattr(self, "token"):
            headers["Authorization"] = f"Bearer {self.token}"
        payload = None if data is None else json.dumps(data).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error), dict(error.headers)

    def wsgi_request(self, path, method="GET", data=None, authenticated=False):
        import wsgi

        route, _, query = path.partition("?")
        payload = b"" if data is None else json.dumps(data).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": route,
            "QUERY_STRING": query,
            "SERVER_NAME": "testserver",
            "SERVER_PORT": "443",
            "HTTP_HOST": "testserver",
            "REMOTE_ADDR": "127.0.0.1",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(payload)),
            "wsgi.input": io.BytesIO(payload),
            "wsgi.url_scheme": "https",
        }
        if authenticated:
            environ["HTTP_AUTHORIZATION"] = f"Bearer {self.token}"
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        body = b"".join(wsgi.application(environ, start_response))
        return int(captured["status"].split()[0]), json.loads(body), captured["headers"]

    def create_student(self):
        status, body, _ = self.request(
            "/api/students",
            method="POST",
            data={
                "StudentNo": "9001",
                "FullName": "طالب اختبار تكاملي",
                "GenderID": 1,
                "BirthDate": "2015-01-01",
                "Mobile1": "0599999999",
                "Address": "عنوان اختباري",
                "StatusID": 1,
                "GovID": 12,
                "GradeID": 4,
                "Section": "أ",
                "AcademicYear": "2026-2027",
                "MonthlyFee": 60,
            },
        )
        self.assertEqual(status, 200, body)
        return body["StudentID"]

    def test_health_headers_and_session_expiry(self):
        status, body, headers = self.request("/api/health", authenticated=False)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

        conn = init_db.get_connection()
        conn.execute(
            "UPDATE UserSessions SET ExpiresAt=datetime('now','localtime','-1 minute') WHERE Token=?",
            (self.token,),
        )
        conn.commit()
        conn.close()
        status, body, _ = self.request("/api/auth/me")
        self.assertEqual(status, 401)
        self.assertIn("تسجيل الدخول", body["error"])

    def test_membership_debt_manual_campaign_and_printed_receipt(self):
        student_id = self.create_student()

        status, body, _ = self.request(
            "/api/class-memberships",
            method="POST",
            data={
                "StudentID": student_id,
                "GradeID": 5,
                "Section": "English",
                "AcademicYear": "2026-2027",
                "Notes": "مجموعة اختبار",
            },
        )
        self.assertEqual(status, 200, body)
        status, grade_students, _ = self.request("/api/grades/students?grade=5")
        self.assertEqual(status, 200)
        self.assertIn(student_id, [row["StudentID"] for row in grade_students])

        status, body, _ = self.request(
            "/api/cash-debts",
            method="POST",
            data={
                "StudentID": student_id,
                "Description": "دين اختبار",
                "PrincipalAmount": 100,
                "DebtDate": "2026-09-19",
            },
        )
        self.assertEqual(status, 200, body)
        debt_id = body["DebtID"]
        status, body, _ = self.request(
            f"/api/cash-debts/{debt_id}/pay",
            method="POST",
            data={"Amount": 35, "PaymentDate": "2026-09-19"},
        )
        self.assertEqual(status, 200, body)
        status, debts, _ = self.request("/api/cash-debts")
        self.assertEqual(status, 200)
        self.assertEqual(debts[0]["PaymentStatus"], "partial")
        self.assertAlmostEqual(debts[0]["Remaining"], 65)

        status, body, _ = self.request(
            "/api/communication-campaigns",
            method="POST",
            data={
                "Title": "قائمة اختبار",
                "MessageBody": "رسالة يدوية",
                "StudentIDs": [student_id],
                "Confirmed": False,
            },
        )
        self.assertEqual(status, 400)
        status, body, _ = self.request(
            "/api/communication-campaigns",
            method="POST",
            data={
                "Title": "قائمة اختبار",
                "MessageBody": "رسالة يدوية",
                "StudentIDs": [student_id],
                "Confirmed": True,
            },
        )
        self.assertEqual(status, 200, body)
        self.assertIn("لم تُرسل", body["message"])
        conn = init_db.get_connection()
        delivery = conn.execute(
            "SELECT DeliveryStatus FROM CommunicationRecipients WHERE CampaignID=?",
            (body["CampaignID"],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(delivery, "جاهز للإرسال اليدوي")

        status, monthly, _ = self.request("/api/monthly-fees?search=9001")
        self.assertEqual(status, 200)
        fee = monthly[0]
        status, payment, _ = self.request(
            "/api/monthly-fees/pay",
            method="POST",
            data={
                "MonthlyFeeID": fee["MonthlyFeeID"],
                "Amount": 20,
                "PaidDate": "2026-09-19",
                "ReceivedBy": "مختبر النظام",
            },
        )
        self.assertEqual(status, 200, payment)
        receipt_id = payment["ReceiptID"]
        status, latest, _ = self.request(f"/api/receipts/latest?student={student_id}")
        self.assertEqual(status, 200)
        self.assertEqual(latest, {})
        status, body, _ = self.request(
            f"/api/receipts/{receipt_id}/print-confirm", method="POST", data={}
        )
        self.assertEqual(status, 200, body)
        status, latest, _ = self.request(f"/api/receipts/latest?student={student_id}")
        self.assertEqual(status, 200)
        self.assertEqual(latest["ReceiptID"], receipt_id)
        self.assertEqual(latest["IsPrinted"], 1)

    def test_archive_requires_reason_and_hides_student_from_active_finance(self):
        student_id = self.create_student()
        status, body, _ = self.request(
            f"/api/students/{student_id}/archive", method="PUT", data={"ArchiveReason": ""}
        )
        self.assertEqual(status, 400)
        status, body, _ = self.request(
            f"/api/students/{student_id}/archive",
            method="PUT",
            data={"ArchiveReason": "انتقال", "ArchiveNote": "اختبار"},
        )
        self.assertEqual(status, 200, body)
        status, active, _ = self.request("/api/students")
        self.assertNotIn(student_id, [row["StudentID"] for row in active])
        status, archived, _ = self.request("/api/students?archived=1")
        self.assertIn(student_id, [row["StudentID"] for row in archived])
        status, monthly, _ = self.request("/api/monthly-fees?search=9001")
        self.assertEqual(monthly, [])

    def test_pythonanywhere_wsgi_adapter(self):
        status, body, headers = self.wsgi_request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("max-age=31536000", headers.get("Strict-Transport-Security", ""))

        status, body, _ = self.wsgi_request("/api/auth/me", authenticated=True)
        self.assertEqual(status, 200)
        self.assertEqual(body["Username"], "admin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
