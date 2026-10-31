#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ترحيل الرسوم الشهرية من أغسطس 2026 إلى سبتمبر 2026 بشكل آمن.
- نقل السجل نفسه (وليس نسخه) حتى لا يتضاعف التحصيل.
- يحتفظ بـ MonthlyFeeID نفسه، لذلك تبقى الإيصالات والملاحظات وسجل الحركات مرتبطة به.
- يحذف سجل سبتمبر التلقائي فقط إذا لم توجد عليه دفعة حقيقية.
- يعيد أي رصيد تم خصمه تلقائياً على فاتورة سبتمبر قبل حذفها.
- يأخذ نسخة احتياطية من قاعدة البيانات قبل أي تعديل.
"""

from __future__ import annotations
import os
import sqlite3
import sys
from datetime import datetime

SOURCE_YEAR = 2026
SOURCE_MONTH = 8
TARGET_YEAR = 2026
TARGET_MONTH = 9
LOCK_WAIT_SECONDS = 120

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
DB_PATH = os.path.join(PROJECT_DIR, "database", "students.db")
BACKUP_DIR = os.path.join(PROJECT_DIR, "database", "backups")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=LOCK_WAIT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={LOCK_WAIT_SECONDS * 1000}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def backup_database() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"students_before_aug_to_sep_{stamp}.db")
    src = connect(DB_PATH)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    return backup_path


def fail(msg: str, conn: sqlite3.Connection | None = None, code: int = 1):
    if conn is not None:
        try:
            conn.rollback()
        except Exception:
            pass
    print("\n❌ " + msg)
    sys.exit(code)


def main():
    if not os.path.exists(DB_PATH):
        fail(f"لم أجد قاعدة البيانات هنا: {DB_PATH}")

    print("=" * 62)
    print("ترحيل الرسوم الشهرية: أغسطس 2026  ➜  سبتمبر 2026")
    print("=" * 62)
    print("⏳ إنشاء نسخة احتياطية...")
    try:
        backup_path = backup_database()
    except sqlite3.OperationalError as e:
        fail(f"تعذر أخذ نسخة احتياطية من قاعدة البيانات: {e}")
    print(f"✅ النسخة الاحتياطية: {backup_path}")

    conn = connect(DB_PATH)
    try:
        required = ["MonthlyFees"]
        missing = [t for t in required if not table_exists(conn, t)]
        if missing:
            fail("الجداول المطلوبة غير موجودة: " + ", ".join(missing), conn)

        conn.execute("BEGIN IMMEDIATE")

        source_rows = conn.execute(
            """
            SELECT * FROM MonthlyFees
            WHERE Year=? AND Month=?
            ORDER BY StudentID
            """,
            (SOURCE_YEAR, SOURCE_MONTH),
        ).fetchall()

        if not source_rows:
            fail("لا توجد رسوم في أغسطس 2026 لترحيلها. لم يتم تغيير أي شيء.", conn)

        source_by_student = {int(r["StudentID"]): r for r in source_rows}
        target_rows = conn.execute(
            """
            SELECT * FROM MonthlyFees
            WHERE Year=? AND Month=?
            ORDER BY StudentID
            """,
            (TARGET_YEAR, TARGET_MONTH),
        ).fetchall()
        target_by_student = {int(r["StudentID"]): r for r in target_rows}

        has_receipts = table_exists(conn, "FeeReceipts")
        has_ledger = table_exists(conn, "StudentFinanceLedger")
        has_accounts = table_exists(conn, "StudentFinanceAccounts")

        conflicts = []
        targets_to_delete = []
        auto_credit_refunds = []  # (student_id, target_fee_id, refund_amount)

        for sid, src in source_by_student.items():
            tgt = target_by_student.get(sid)
            if not tgt:
                continue

            tgt_id = int(tgt["MonthlyFeeID"])
            target_paid = float(tgt["AmountPaid"] or 0)

            receipt_count = 0
            if has_receipts:
                receipt_count = int(conn.execute(
                    """
                    SELECT COUNT(*) FROM FeeReceipts
                    WHERE FeeKind='monthly' AND FeeID=?
                    """,
                    (tgt_id,),
                ).fetchone()[0] or 0)

            ledger_rows = []
            if has_ledger:
                ledger_rows = conn.execute(
                    """
                    SELECT LedgerID, EventType, Amount, CreditChange, Notes
                    FROM StudentFinanceLedger
                    WHERE ReferenceKind='monthly' AND ReferenceID=?
                    ORDER BY LedgerID
                    """,
                    (tgt_id,),
                ).fetchall()

            # المسموح حذفه تلقائياً: خصم رصيد آلي فقط على فاتورة سبتمبر المولدة.
            auto_credit_rows = []
            non_auto_ledger = []
            for lr in ledger_rows:
                event_type = str(lr["EventType"] or "").strip()
                change = float(lr["CreditChange"] or 0)
                notes = str(lr["Notes"] or "")
                is_auto_credit = (
                    event_type == "استخدام رصيد"
                    and change < -0.0001
                    and ("تلقائي" in notes or "الرصيد" in notes)
                )
                if is_auto_credit:
                    auto_credit_rows.append(lr)
                else:
                    non_auto_ledger.append(lr)

            auto_credit_used = -sum(float(r["CreditChange"] or 0) for r in auto_credit_rows)

            # أي إيصال نقدي أو حركة غير تلقائية على سبتمبر تعني أن هناك نشاطاً حقيقياً؛ لا نمسحه.
            if receipt_count > 0 or non_auto_ledger:
                conflicts.append((sid, tgt_id, receipt_count, len(non_auto_ledger), target_paid))
                continue

            # إذا AmountPaid في سبتمبر أكبر من الرصيد الآلي المعروف، نعتبره دفعاً لا يجوز مسحه.
            if target_paid > auto_credit_used + 0.01:
                conflicts.append((sid, tgt_id, receipt_count, len(non_auto_ledger), target_paid))
                continue

            if auto_credit_used > 0.0001:
                if not has_accounts:
                    conflicts.append((sid, tgt_id, receipt_count, len(non_auto_ledger), target_paid))
                    continue
                auto_credit_refunds.append((sid, tgt_id, auto_credit_used, [int(r["LedgerID"]) for r in auto_credit_rows]))

            targets_to_delete.append((sid, tgt_id))

        if conflicts:
            print("\n⚠️ وُجدت حركة فعلية على رسوم سبتمبر لبعض الطلاب، لذلك أوقفت العملية حفاظاً على البيانات.")
            print("أول السجلات المتعارضة:")
            for sid, feeid, rc, lc, paid in conflicts[:20]:
                print(f"  StudentID={sid} | SeptemberFeeID={feeid} | AmountPaid={paid:.2f} | Receipts={rc} | OtherLedger={lc}")
            fail("لم يتم ترحيل أي شيء. النسخة الاحتياطية موجودة، لكن قاعدة البيانات الأصلية لم تتغير.", conn)

        # رد الرصيد الذي استُخدم تلقائياً في فاتورة سبتمبر المراد حذفها.
        total_refunded_credit = 0.0
        for sid, tgt_id, refund, ledger_ids in auto_credit_refunds:
            conn.execute(
                """
                INSERT INTO StudentFinanceAccounts(StudentID, CreditBalance, UpdatedAt)
                VALUES (?, ?, datetime('now','localtime'))
                ON CONFLICT(StudentID) DO UPDATE SET
                    CreditBalance=COALESCE(StudentFinanceAccounts.CreditBalance,0)+excluded.CreditBalance,
                    UpdatedAt=excluded.UpdatedAt
                """,
                (sid, refund),
            )
            if ledger_ids:
                placeholders = ",".join("?" for _ in ledger_ids)
                conn.execute(
                    f"DELETE FROM StudentFinanceLedger WHERE LedgerID IN ({placeholders})",
                    ledger_ids,
                )
            total_refunded_credit += refund

        # حذف سجل سبتمبر التلقائي للطلاب الموجودين في أغسطس، لكي نتمكن من نقل سجل أغسطس بنفس الـID.
        deleted_sep = 0
        for sid, tgt_id in targets_to_delete:
            cur = conn.execute("DELETE FROM MonthlyFees WHERE MonthlyFeeID=?", (tgt_id,))
            deleted_sep += cur.rowcount

        # النقل الفعلي: نفس السجل ونفس الـMonthlyFeeID ونفس Amount/AmountPaid/Notes/Photo/PaidDate.
        cur = conn.execute(
            """
            UPDATE MonthlyFees
            SET Year=?, Month=?
            WHERE Year=? AND Month=?
            """,
            (TARGET_YEAR, TARGET_MONTH, SOURCE_YEAR, SOURCE_MONTH),
        )
        moved = cur.rowcount

        # علامة إدارية تمنع الالتباس عند المراجعة، بدون تغيير ملاحظات الفواتير نفسها.
        if table_exists(conn, "AppSettings"):
            key = "migration_monthly_fees_2026_08_to_2026_09"
            value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT OR REPLACE INTO AppSettings(SettingKey, SettingValue) VALUES (?,?)",
                (key, value),
            )

        # تحقق نهائي قبل الحفظ.
        aug_count = int(conn.execute(
            "SELECT COUNT(*) FROM MonthlyFees WHERE Year=? AND Month=?",
            (SOURCE_YEAR, SOURCE_MONTH),
        ).fetchone()[0])
        sep_count = int(conn.execute(
            "SELECT COUNT(*) FROM MonthlyFees WHERE Year=? AND Month=?",
            (TARGET_YEAR, TARGET_MONTH),
        ).fetchone()[0])

        if moved != len(source_rows) or aug_count != 0:
            fail(
                f"فشل التحقق النهائي (source={len(source_rows)}, moved={moved}, august_left={aug_count}). تم التراجع تلقائياً.",
                conn,
            )

        conn.commit()

        print("\n✅ تم ترحيل رسوم أغسطس 2026 إلى سبتمبر 2026 بنجاح")
        print(f"• عدد سجلات أغسطس التي تم نقلها: {moved}")
        print(f"• سجلات سبتمبر التلقائية التي تم استبدالها: {deleted_sep}")
        print(f"• رصيد أُعيد للطلاب قبل حذف فواتير سبتمبر التلقائية: {total_refunded_credit:.2f} ₪")
        print(f"• عدد سجلات أغسطس المتبقية: {aug_count}")
        print(f"• إجمالي سجلات سبتمبر الآن: {sep_count}")
        print("• الإيصالات والملاحظات وحالة السداد بقيت مرتبطة بنفس MonthlyFeeID.")
        print("• لم يتم نسخ التحصيل مرتين، لذلك لا يتضاعف إجمالي المقبوضات.")
        print(f"\n🛡️ النسخة الاحتياطية: {backup_path}")

    except sqlite3.OperationalError as e:
        try:
            conn.rollback()
        except Exception:
            pass
        if "locked" in str(e).lower():
            fail(
                "قاعدة البيانات مقفلة من الموقع. عطّل Web app مؤقتاً في PythonAnywhere ثم أعد تشغيل السكربت. "
                f"التفاصيل: {e}",
                None,
            )
        fail(f"خطأ SQLite: {e}", conn)
    except Exception as e:
        fail(f"حدث خطأ غير متوقع: {type(e).__name__}: {e}", conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
