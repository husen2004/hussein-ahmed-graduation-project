"""Create a clean, deterministic demonstration database for the graduation project.

The generated records are fictional and must never be mixed with a production
school database. Run from the project directory with:

    python tools/create_demo_data.py --force
"""
from __future__ import annotations

import argparse
import hashlib
import secrets
import sys
from datetime import date, timedelta
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from database import init_db  # noqa: E402


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 600000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="replace the existing demo database")
    args = parser.parse_args()

    database_path = Path(init_db.DB_PATH).resolve()
    expected_parent = (PROJECT_DIR / "database").resolve()
    if database_path.name != "students.db" or database_path.parent != expected_parent:
        raise SystemExit("Refusing to write outside the project database directory")
    if database_path.exists():
        if not args.force:
            raise SystemExit("students.db already exists; use --force to replace the demo database")
        database_path.unlink()

    init_db.init_database()
    conn = init_db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO Schools (SchoolName, Region, Phone) VALUES (?,?,?)",
            ("المدرسة النموذجية", "غزة", "0000000000"),
        )
        school_id = cur.lastrowid

        students = [
            ("0001", "آدم سامر الخطيب", 1, "2016-02-15", "0590000001", 4, "أ"),
            ("0002", "ليان فادي منصور", 2, "2016-07-21", "0590000002", 4, "أ"),
            ("0003", "يوسف مازن سالم", 1, "2015-05-10", "0590000003", 5, "أ"),
            ("0004", "سارة رائد النجار", 2, "2015-09-03", "0590000004", 5, "ب"),
            ("0005", "كريم وائل حسن", 1, "2014-11-27", "0590000005", 6, "أ"),
            ("0006", "نور أحمد موسى", 2, "2014-01-18", "0590000006", 6, "ب"),
            ("0007", "عمر ناصر علي", 1, "2013-04-06", "0590000007", 7, "أ"),
            ("0008", "مريم إياد عوض", 2, "2013-08-12", "0590000008", 7, "ب"),
            ("0009", "سليم خالد محمود", 1, "2012-03-22", "0590000009", 8, "أ"),
            ("0010", "جنى مؤيد شحادة", 2, "2012-12-01", "0590000010", 8, "ب"),
            ("0011", "ياسين أمجد حمد", 1, "2011-06-14", "0590000011", 9, "أ"),
            ("0012", "تالا حسام زيد", 2, "2011-10-30", "0590000012", 9, "ب"),
            ("0013", "طالب تجريبي مؤرشف", 1, "2010-01-01", "0590000013", 10, "أ"),
        ]

        student_ids: list[int] = []
        for index, (number, name, gender, birth, mobile, grade, section) in enumerate(students):
            archived = 1 if number == "0013" else 0
            cur.execute(
                """
                INSERT INTO Students
                (StudentNo, FullName, GenderID, BirthDate, Mobile1, Address,
                 StatusID, GovID, Notes, MonthlyFee, IsArchived, ArchivedDate,
                 ArchiveReason, ArchiveNote)
                VALUES (?,?,?,?,?,'عنوان تجريبي',1,12,?,60,?,?,?,?)
                """,
                (
                    number,
                    name,
                    gender,
                    birth,
                    mobile,
                    "بيانات تجريبية لمشروع التخرج",
                    archived,
                    date.today().isoformat() if archived else None,
                    "انتقال إلى مدرسة أخرى" if archived else None,
                    "سجل تجريبي لعرض الأرشفة" if archived else None,
                ),
            )
            student_id = cur.lastrowid
            student_ids.append(student_id)
            cur.execute(
                """
                INSERT INTO Enrollments (StudentID, SchoolID, GradeID, Section, AcademicYear)
                VALUES (?,?,?,?,?)
                """,
                (student_id, school_id, grade, section, "2026-2027"),
            )
            cur.execute(
                "INSERT INTO Parents (ParentName, Relation, Mobile, Job) VALUES (?,?,?,?)",
                (f"ولي أمر {name}", "أب", mobile, "بيانات تجريبية"),
            )
            cur.execute(
                "INSERT INTO ParentStudents (ParentID, StudentID) VALUES (?,?)",
                (cur.lastrowid, student_id),
            )
            if index in (5, 8):
                cur.execute(
                    "INSERT INTO HealthStatus (StudentID, HealthType, Notes) VALUES (?,?,?)",
                    (student_id, "مرض مزمن (يحتاج رعاية مستمرة)", "مثال تدريبي فقط"),
                )
            if index in (3, 9):
                cur.execute(
                    "INSERT INTO Orphans (StudentID, OrphanType, Notes) VALUES (?,?,?)",
                    (student_id, "يتيم الأب", "مثال تدريبي فقط"),
                )

        cur.execute(
            """
            INSERT INTO Users (Username, PasswordHash, FullName, Role, IsActive, CanEnterMarks)
            VALUES (?,?,?,?,1,1)
            """,
            ("teacher", password_hash(secrets.token_urlsafe(32)), "معلم تجريبي", "teacher"),
        )
        teacher_id = cur.lastrowid
        cur.execute(
            "INSERT INTO TeacherProfiles (UserID, Phone, SerialNo, Subject, Notes) VALUES (?,?,?,?,?)",
            (teacher_id, "0590000099", "0001", "رياضيات", "حساب العرض التجريبي"),
        )
        for grade_id in (4, 5):
            cur.execute(
                "INSERT INTO TeacherGrades (UserID, GradeID, Section) VALUES (?,?,NULL)",
                (teacher_id, grade_id),
            )
        math_id = cur.execute(
            "SELECT SubjectID FROM Subjects WHERE SubjectName='رياضيات'"
        ).fetchone()[0]
        cur.execute(
            "INSERT INTO TeacherSubjects (UserID, SubjectID) VALUES (?,?)",
            (teacher_id, math_id),
        )

        cur.executemany(
            """
            INSERT INTO StudentClassMemberships
            (StudentID, GradeID, Section, AcademicYear, Notes)
            VALUES (?,?,?,?,?)
            """,
            [
                (student_ids[0], 5, "English", "2026-2027", "مجموعة اللغة الإنجليزية"),
                (student_ids[1], 6, "English", "2026-2027", "مجموعة المحادثة"),
            ],
        )

        for student_id in student_ids[:12]:
            for offset in range(5):
                attendance_date = (date.today() - timedelta(days=offset)).isoformat()
                status = "غائب" if (student_id + offset) % 7 == 0 else "حاضر"
                cur.execute(
                    """
                    INSERT OR IGNORE INTO Attendance
                    (StudentID, AttendanceDate, Status, Notes, RecordedBy)
                    VALUES (?,?,?,?,?)
                    """,
                    (student_id, attendance_date, status, "بيانات عرض", teacher_id),
                )
            cur.execute(
                """
                INSERT INTO StudentMarks
                (StudentID, TeacherUserID, Subject, ExamName, Mark, MaxMark, Notes)
                VALUES (?,?,?,'اختبار قصير',?,100,'بيانات عرض')
                """,
                (student_id, teacher_id, "رياضيات", 72 + (student_id * 3) % 27),
            )

        cur.executemany(
            """
            INSERT INTO CashDebts
            (StudentID, Description, PrincipalAmount, DebtDate, DueDate, Notes, CreatedBy)
            VALUES (?,?,?,date('now'),date('now','+30 day'),?,?)
            """,
            [
                (student_ids[0], "سلفة كتب", 40, "مثال لدين مسدد", 1),
                (student_ids[2], "زي مدرسي", 150, "مثال لدين جزئي", 1),
                (student_ids[3], "مواصلات", 80, "مثال لدين غير مدفوع", 1),
            ],
        )
        debt_rows = cur.execute("SELECT DebtID, PrincipalAmount FROM CashDebts ORDER BY DebtID").fetchall()
        cur.executemany(
            """
            INSERT INTO CashDebtPayments (DebtID, Amount, PaymentDate, ReceivedBy, Notes)
            VALUES (?,?,date('now'),'أمين الصندوق','دفعة تجريبية')
            """,
            [(debt_rows[0][0], 40), (debt_rows[1][0], 50)],
        )

        conn.commit()
    finally:
        conn.close()

    init_db.generate_missing_monthly_fees()

    conn = init_db.get_connection()
    latest = conn.execute(
        """
        SELECT mf.MonthlyFeeID, mf.StudentID, mf.Amount
        FROM MonthlyFees mf
        JOIN (
            SELECT StudentID, MAX(Year*100+Month) AS YM FROM MonthlyFees GROUP BY StudentID
        ) x ON x.StudentID=mf.StudentID AND x.YM=mf.Year*100+mf.Month
        ORDER BY mf.StudentID
        """
    ).fetchall()
    conn.close()
    for index, row in enumerate(latest):
        if index < 4:
            init_db.finance_process_payment("monthly", row["MonthlyFeeID"], row["Amount"], received_by="أمين الصندوق")
        elif index < 7:
            init_db.finance_process_payment("monthly", row["MonthlyFeeID"], float(row["Amount"]) / 2, received_by="أمين الصندوق")

    conn = init_db.get_connection()
    try:
        receipt_ids = [r[0] for r in conn.execute("SELECT ReceiptID FROM FeeReceipts ORDER BY ReceiptID").fetchall()]
        for receipt_id in receipt_ids[:-1]:
            conn.execute(
                "UPDATE FeeReceipts SET IsPrinted=1, PrintedAt=datetime('now','localtime') WHERE ReceiptID=?",
                (receipt_id,),
            )
        conn.execute(
            """
            INSERT INTO CommunicationCampaigns
            (Title, MessageBody, Status, RecipientCount, CreatedBy, ConfirmedAt)
            VALUES ('تذكير تجريبي بالرسوم','رسالة تجريبية للإرسال اليدوي فقط',
                    'confirmed_manual',2,1,datetime('now','localtime'))
            """
        )
        campaign_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.executemany(
            """
            INSERT INTO CommunicationRecipients (CampaignID, StudentID, Mobile)
            VALUES (?,?,?)
            """,
            [
                (campaign_id, student_ids[6], "0590000007"),
                (campaign_id, student_ids[7], "0590000008"),
            ],
        )
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    print(f"Demo database created at {database_path}")
    print(f"Integrity check: {integrity}")
    print("Set the admin password: python tools/set_admin_password.py --username admin")
    print("Set the teacher password: python tools/set_admin_password.py --username teacher")


if __name__ == "__main__":
    main()
