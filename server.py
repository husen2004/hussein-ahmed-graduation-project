"""
نظام إدارة الطلاب - الخادم الخلفي
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database.init_db import init_database, get_connection, generate_student_no, regenerate_carry_over_from, generate_missing_monthly_fees, ensure_current_monthly_fee, DB_PATH, finance_get_credit, finance_process_payment, finance_account_snapshot, finance_ledger_rows, ensure_student_finance_schema
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import sqlite3
from datetime import datetime
import base64
import io
import hashlib
import secrets
BACKUP_DIR = os.path.abspath(
    os.environ.get("AFAQ_BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH), "backups"))
)
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False



def hash_password(password):
    salt = secrets.token_hex(16)
    iterations = 600000
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password, stored):
    try:
        algo, iterations, salt, digest = stored.split('$')
        dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), int(iterations))
        return secrets.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def teacher_grade_filter(user, params):
    """يرجع شرط SQL لتقييد بيانات المعلم على صفوفه وشُعبه فقط."""
    if not user or user.get('Role') != 'teacher':
        return '', params
    rows = query_db("SELECT GradeID, Section FROM TeacherGrades WHERE UserID=?", (user['UserID'],))
    if not rows:
        return ' AND 1=0 ', params
    conds = []
    for r in rows:
        if r.get('Section'):
            conds.append('(e.GradeID=? AND e.Section=?)')
            params += [r['GradeID'], r['Section']]
        else:
            conds.append('(e.GradeID=?)')
            params.append(r['GradeID'])
    return ' AND (' + ' OR '.join(conds) + ') ', params


def student_allowed_for_user(user, student_id):
    if not user or user.get('Role') == 'admin':
        return True
    rows = query_db("""
        SELECT 1 FROM Enrollments e
        JOIN TeacherGrades tg ON tg.GradeID=e.GradeID AND tg.UserID=?
        WHERE e.StudentID=? AND (tg.Section IS NULL OR tg.Section='' OR tg.Section=e.Section)
        LIMIT 1
    """, (user['UserID'], student_id))
    return bool(rows)


def teacher_student_scope_sql(user, alias='e'):
    """شرط SQL يحصر الطلاب على الصفوف/الشُعب المخصصة للمعلم."""
    if not user or user.get('Role') != 'teacher':
        return '', []
    rows = query_db("SELECT GradeID, Section FROM TeacherGrades WHERE UserID=?", (user['UserID'],))
    if not rows:
        return ' AND 1=0 ', []
    conds, params = [], []
    for r in rows:
        if r.get('Section'):
            conds.append(f'({alias}.GradeID=? AND {alias}.Section=?)')
            params += [r['GradeID'], r['Section']]
        else:
            conds.append(f'({alias}.GradeID=?)')
            params.append(r['GradeID'])
    return ' AND (' + ' OR '.join(conds) + ') ', params


def teacher_subject_names(user_id):
    rows = query_db("""
        SELECT sub.SubjectName FROM TeacherSubjects ts
        JOIN Subjects sub ON sub.SubjectID=ts.SubjectID
        WHERE ts.UserID=? ORDER BY sub.SubjectName
    """, (user_id,))
    return [r['SubjectName'] for r in rows]

def next_teacher_serial():
    """يولّد الرقم التسلسلي التالي للمعلم بصيغة موحّدة: 0001، 0002، ..."""
    rows = query_db("""
        SELECT SerialNo FROM TeacherProfiles
        WHERE SerialNo IS NOT NULL AND TRIM(SerialNo)!=''
    """)
    highest = 0
    for row in rows:
        digits = ''.join(ch for ch in str(row.get('SerialNo') or '') if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return f"{highest + 1:04d}"

def build_notifications(user):
    """
    يبني قائمة الإشعارات الذكية للنظام:
    1) طلاب تبقى أسبوع أو أقل على موعد استحقاق رسومهم (رسوم إضافية بتاريخ استحقاق محدد،
       أو الرسم الشهري الحالي الذي يقترب نهاية شهره) وهي غير مسددة بعد.
    2) طلاب لم يسددوا أي رسم شهري منذ 4 أشهر فأكثر.
    3) طلاب لا يوجد لهم ولي أمر مسجل في النظام.
    4) طلاب تنقص بياناتهم الأساسية (رقم الهوية، تاريخ الميلاد، الجوال، العنوان، المحافظة، الجنس، الصف).
    5) طلاب لم يسددوا الرسم الشهري للشهر الحالي، وقد تجاوزنا يوم 10 من الشهر.
    6) طلاب تغيّبوا 3 أيام دراسية متواصلة (يُنبَّه عنهم في اليوم الثالث فقط، ولا يتكرر التنبيه
       في الأيام التالية طالما استمر الغياب).
    """
    scope, sp = teacher_student_scope_sql(user, 'e')
    scope = " AND s.IsArchived = 0" + scope  # لا نُنبّه عن طلاب مؤرشفين
    is_teacher = user and user.get('Role') == 'teacher'

    notifications = []

    # ---------- 1) اقتراب موعد الاستحقاق (خلال 7 أيام) ----------
    # أ) رسوم إضافية (Fees) لها تاريخ استحقاق صريح
    due_params = list(sp)
    due_sql = f"""
        SELECT s.StudentID, s.FullName, s.StudentNo, gr.GradeName, e.Section,
               f.DueDate, (f.Amount - COALESCE(f.AmountPaid,0)) as Remaining, f.FeeType
        FROM Fees f
        JOIN Students s ON f.StudentID = s.StudentID
        LEFT JOIN Enrollments e ON s.StudentID = e.StudentID
        LEFT JOIN Grades gr ON e.GradeID = gr.GradeID
        WHERE (f.AmountPaid IS NULL OR f.AmountPaid < f.Amount)
          AND f.DueDate IS NOT NULL AND f.DueDate != ''
          AND date(f.DueDate) BETWEEN date('now') AND date('now', '+7 day')
    """ + scope
    for r in query_db(due_sql, due_params):
        days_left = None
        try:
            days_left = (datetime.strptime(r['DueDate'][:10], '%Y-%m-%d').date() - datetime.now().date()).days
        except Exception:
            pass
        notifications.append({
            "type": "due_soon",
            "icon": "⏰",
            "studentId": r['StudentID'],
            "studentName": r['FullName'],
            "studentNo": r['StudentNo'],
            "gradeName": r['GradeName'],
            "message": f"تبقى {days_left if days_left is not None else ''} يوم/أيام على موعد استحقاق ({r['FeeType'] or 'رسوم'}) للطالب {r['FullName']}".strip(),
            "amount": r['Remaining'],
            "dueDate": r['DueDate'],
        })

    # ب) الرسم الشهري للشهر الحالي: نعتبر أن موعد الاستحقاق هو آخر يوم من الشهر الحالي
    today = datetime.now()
    from calendar import monthrange
    last_day = monthrange(today.year, today.month)[1]
    days_to_month_end = last_day - today.day
    if 0 <= days_to_month_end <= 7:
        mf_params = [today.year, today.month] + list(sp)
        mf_sql = f"""
            SELECT s.StudentID, s.FullName, s.StudentNo, gr.GradeName, e.Section,
                   mf.Amount, mf.AmountPaid
            FROM MonthlyFees mf
            JOIN Students s ON mf.StudentID = s.StudentID
            LEFT JOIN Enrollments e ON s.StudentID = e.StudentID
            LEFT JOIN Grades gr ON e.GradeID = gr.GradeID
            WHERE mf.Year=? AND mf.Month=?
              AND (mf.AmountPaid IS NULL OR mf.AmountPaid < mf.Amount)
        """ + scope
        for r in query_db(mf_sql, mf_params):
            notifications.append({
                "type": "due_soon",
                "icon": "⏰",
                "studentId": r['StudentID'],
                "studentName": r['FullName'],
                "studentNo": r['StudentNo'],
                "gradeName": r['GradeName'],
                "message": f"تبقى {days_to_month_end} يوم/أيام على نهاية موعد دفع الرسم الشهري للطالب {r['FullName']}",
                "amount": (r['Amount'] or 0) - (r['AmountPaid'] or 0),
                "dueDate": f"{today.year}-{today.month:02d}-{last_day:02d}",
            })

    # ---------- 2) لم يدفع منذ 4 أشهر فأكثر ----------
    unpaid_params = list(sp)
    unpaid_sql = f"""
        SELECT s.StudentID, s.FullName, s.StudentNo, gr.GradeName, e.Section,
               MAX(CASE WHEN mf.Amount > 0 AND mf.AmountPaid >= mf.Amount THEN mf.Year*12+mf.Month END) as LastPaidYM,
               MIN(CASE WHEN mf.Amount > 0 THEN mf.Year*12+mf.Month END) as FirstYM
        FROM Students s
        JOIN MonthlyFees mf ON mf.StudentID = s.StudentID
        LEFT JOIN Enrollments e ON s.StudentID = e.StudentID
        LEFT JOIN Grades gr ON e.GradeID = gr.GradeID
        WHERE 1=1
    """ + scope + " GROUP BY s.StudentID"
    current_ym = today.year * 12 + today.month
    for r in query_db(unpaid_sql, unpaid_params):
        if not r['FirstYM']:
            continue
        last_paid = r['LastPaidYM'] if r['LastPaidYM'] else (r['FirstYM'] - 1)
        months_diff = current_ym - last_paid
        if months_diff >= 4:
            notifications.append({
                "type": "overdue",
                "icon": "🔴",
                "studentId": r['StudentID'],
                "studentName": r['FullName'],
                "studentNo": r['StudentNo'],
                "gradeName": r['GradeName'],
                "message": f"الطالب {r['FullName']} لم يسدد الرسم الشهري منذ {months_diff} أشهر",
                "monthsUnpaid": months_diff,
            })

    # ---------- 3) لا يوجد له ولي أمر ----------
    noparent_params = list(sp)
    noparent_sql = f"""
        SELECT s.StudentID, s.FullName, s.StudentNo, gr.GradeName, e.Section
        FROM Students s
        LEFT JOIN Enrollments e ON s.StudentID = e.StudentID
        LEFT JOIN Grades gr ON e.GradeID = gr.GradeID
        LEFT JOIN ParentStudents ps ON ps.StudentID = s.StudentID
        LEFT JOIN Parents p ON p.ParentID = ps.ParentID
        WHERE p.ParentID IS NULL
    """ + scope
    for r in query_db(noparent_sql, noparent_params):
        notifications.append({
            "type": "no_guardian",
            "icon": "⚠️",
            "studentId": r['StudentID'],
            "studentName": r['FullName'],
            "studentNo": r['StudentNo'],
            "gradeName": r['GradeName'],
            "message": f"لا يوجد ولي أمر مسجل للطالب {r['FullName']}",
        })

    # ---------- 4) بيانات ناقصة في ملف الطالب ----------
    missing_params = list(sp)
    missing_sql = f"""
        SELECT s.StudentID, s.FullName, s.StudentNo, gr.GradeName, e.Section,
               s.NationalID, s.BirthDate, s.Mobile1, s.Address, s.GovID, s.GenderID, e.GradeID
        FROM Students s
        LEFT JOIN Enrollments e ON s.StudentID = e.StudentID
        LEFT JOIN Grades gr ON e.GradeID = gr.GradeID
        WHERE 1=1
    """ + scope
    # كل حقل هنا واسمه الظاهر للمستخدم عند نقصه
    required_fields = [
        ('NationalID', 'رقم الهوية'),
        ('BirthDate', 'تاريخ الميلاد'),
        ('Mobile1', 'رقم الجوال'),
        ('Address', 'العنوان'),
        ('GovID', 'المحافظة'),
        ('GenderID', 'الجنس'),
        ('GradeID', 'الصف الدراسي'),
    ]
    for r in query_db(missing_sql, missing_params):
        missing_names = [label for field, label in required_fields if not r.get(field)]
        if missing_names:
            notifications.append({
                "type": "missing_data",
                "icon": "📝",
                "studentId": r['StudentID'],
                "studentName": r['FullName'],
                "studentNo": r['StudentNo'],
                "gradeName": r['GradeName'],
                "message": f"بيانات ناقصة للطالب {r['FullName']}: " + "، ".join(missing_names),
                "missingFields": missing_names,
            })

    # ---------- 5) عدم تسديد الرسم الشهري قبل يوم 10 من الشهر الحالي ----------
    ARABIC_MONTHS = {
        1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
        7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
    }
    if today.day > 10:
        unpaid10_params = [today.year, today.month] + list(sp)
        unpaid10_sql = f"""
            SELECT s.StudentID, s.FullName, s.StudentNo, gr.GradeName, e.Section,
                   mf.Amount, mf.AmountPaid
            FROM MonthlyFees mf
            JOIN Students s ON mf.StudentID = s.StudentID
            LEFT JOIN Enrollments e ON s.StudentID = e.StudentID
            LEFT JOIN Grades gr ON e.GradeID = gr.GradeID
            WHERE mf.Year=? AND mf.Month=?
              AND mf.Amount > 0
              AND (mf.AmountPaid IS NULL OR mf.AmountPaid < mf.Amount)
        """ + scope
        for r in query_db(unpaid10_sql, unpaid10_params):
            remaining = (r['Amount'] or 0) - (r['AmountPaid'] or 0)
            notifications.append({
                "type": "unpaid_before_10",
                "icon": "💰",
                "studentId": r['StudentID'],
                "studentName": r['FullName'],
                "studentNo": r['StudentNo'],
                "gradeName": r['GradeName'],
                "message": f"الطالب {r['FullName']} لم يسدد الرسم الشهري لشهر {ARABIC_MONTHS.get(today.month, today.month)} حتى الآن، وقد تجاوزنا يوم 10 من الشهر",
                "amount": remaining,
            })

    # ---------- 6) غياب 3 أيام متتالية (يُنبَّه فقط في اليوم الثالث، ولا يتكرر بعده) ----------
    absent3_params = list(sp)
    absent3_candidates_sql = f"""
        SELECT DISTINCT s.StudentID, s.FullName, s.StudentNo, gr.GradeName, e.Section
        FROM Students s
        JOIN Attendance a ON a.StudentID = s.StudentID
        LEFT JOIN Enrollments e ON s.StudentID = e.StudentID
        LEFT JOIN Grades gr ON e.GradeID = gr.GradeID
        WHERE a.Status = 'غائب'
          AND date(a.AttendanceDate) <= date('now')
    """ + scope
    for r in query_db(absent3_candidates_sql, absent3_params):
        recs = query_db("""
            SELECT AttendanceDate, Status FROM Attendance
            WHERE StudentID = ? AND date(AttendanceDate) <= date('now')
            ORDER BY date(AttendanceDate) DESC
            LIMIT 4
        """, (r['StudentID'],))
        # يجب توفر آخر 3 سجلات على الأقل وكلها "غائب"
        if len(recs) < 3 or any(x['Status'] != 'غائب' for x in recs[:3]):
            continue
        # وحتى لا يتكرر التنبيه كل يوم بعد ذلك: نتأكد أن اليوم الرابع (إن وُجد) ليس غائباً أيضاً
        # (أي أن هذا هو بالضبط اليوم الثالث من الغياب المتواصل، وليس الرابع أو أكثر)
        if len(recs) >= 4 and recs[3]['Status'] == 'غائب':
            continue
        notifications.append({
            "type": "absent_3days",
            "icon": "🚫",
            "studentId": r['StudentID'],
            "studentName": r['FullName'],
            "studentNo": r['StudentNo'],
            "gradeName": r['GradeName'],
            "message": f"الطالب {r['FullName']} غائب منذ 3 أيام متواصلة",
            "absentDates": [x['AttendanceDate'] for x in recs[:3]],
        })

    counts = {
        "due_soon": sum(1 for n in notifications if n['type'] == 'due_soon'),
        "overdue": sum(1 for n in notifications if n['type'] == 'overdue'),
        "no_guardian": sum(1 for n in notifications if n['type'] == 'no_guardian'),
        "missing_data": sum(1 for n in notifications if n['type'] == 'missing_data'),
        "unpaid_before_10": sum(1 for n in notifications if n['type'] == 'unpaid_before_10'),
        "absent_3days": sum(1 for n in notifications if n['type'] == 'absent_3days'),
    }
    return {
        "notifications": notifications,
        "total": len(notifications),
        "counts": counts,
    }


def save_grade_subjects(grade_id, subjects):
    execute_db("DELETE FROM GradeSubjects WHERE GradeID=?", (grade_id,))
    for item in subjects or []:
        if isinstance(item, dict):
            sid = item.get('SubjectID')
            name = (item.get('SubjectName') or '').strip()
        else:
            sid = item
            name = ''
        if not sid and name:
            execute_db("INSERT OR IGNORE INTO Subjects (SubjectName) VALUES (?)", (name,))
            row = query_db("SELECT SubjectID FROM Subjects WHERE SubjectName=?", (name,), one=True)
            sid = row['SubjectID'] if row else None
        if sid:
            execute_db("INSERT OR IGNORE INTO GradeSubjects (GradeID,SubjectID) VALUES (?,?)", (grade_id, sid))

def save_teacher_subjects(user_id, subjects):
    execute_db("DELETE FROM TeacherSubjects WHERE UserID=?", (user_id,))
    for item in subjects or []:
        if isinstance(item, dict):
            name = (item.get('SubjectName') or '').strip()
            sid = item.get('SubjectID')
        else:
            name = str(item or '').strip()
            sid = None
        if not sid and name:
            execute_db("INSERT OR IGNORE INTO Subjects (SubjectName) VALUES (?)", (name,))
            row = query_db("SELECT SubjectID FROM Subjects WHERE SubjectName=?", (name,), one=True)
            sid = row['SubjectID'] if row else None
        if sid:
            execute_db("INSERT OR IGNORE INTO TeacherSubjects (UserID,SubjectID) VALUES (?,?)", (user_id, sid))

def query_db(sql, params=(), one=False):
    conn = get_connection()
    cur = conn.execute(sql, params)
    rv = cur.fetchall()
    conn.close()
    if one:
        return dict(rv[0]) if rv else None
    return [dict(r) for r in rv]


def execute_db(sql, params=()):
    conn = get_connection()
    cur = conn.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def log_activity(user_id, action, method=None, path=None, details=None, username=None, full_name=None):
    """يحفظ نشاط المستخدم دون أن يعطل العملية الأساسية عند حدوث خطأ في السجل."""
    try:
        if user_id and (not username or not full_name):
            u = query_db("SELECT Username, FullName FROM Users WHERE UserID=?", (user_id,), one=True) or {}
            username = username or u.get('Username')
            full_name = full_name or u.get('FullName')
        execute_db("""
            INSERT INTO ActivityLogs (UserID, Username, FullName, Action, Method, Path, Details)
            VALUES (?,?,?,?,?,?,?)
        """, (user_id, username, full_name, action, method, path, details))
    except Exception:
        pass


def describe_api_action(method, path, payload=None):
    """وصف عربي مختصر يظهر في سجل النشاطات."""
    payload = payload if isinstance(payload, dict) else {}
    message = payload.get('message')
    if message:
        return message
    if path == '/api/marks': return 'حفظ أو تعديل علامة طالب'
    if path in ('/api/attendance', '/api/attendance/bulk'): return 'تسجيل الحضور والغياب'
    if path.endswith('/graduation-project'): return 'حفظ مشروع تخرج الطالب'
    if path.startswith('/api/users/'): return 'تعديل بيانات مستخدم أو صلاحياته'
    if path == '/api/users': return 'إضافة مستخدم جديد'
    if path.startswith('/api/students/'): return 'تعديل بيانات مرتبطة بطالب'
    if path == '/api/students': return 'إضافة طالب جديد'
    if path == '/api/class-memberships': return 'إضافة عضوية دراسية إضافية'
    if path.startswith('/api/cash-debts'): return 'تحديث سجل الديون النقدية'
    if path == '/api/communication-campaigns': return 'اعتماد قائمة رسائل يدوية'
    if path.endswith('/print-confirm'): return 'اعتماد سند قبض بعد الطباعة'
    return f'{method} {path}'


def user_can_enter_marks(user):
    if not user or user.get('Role') != 'teacher':
        return True
    return bool(user.get('CanEnterMarks', 1))


def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def safe_backup_filename(prefix="students_backup"):
    ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    return f"{prefix}_{ts}.db"


def create_backup_file(dest_path):
    """ينشئ نسخة متسقة (snapshot) من قاعدة البيانات الحيّة في dest_path باستخدام SQLite backup API
    وهو أسلم من نسخ الملف مباشرة لأنه يتعامل بشكل صحيح مع أي عمليات كتابة جارية."""
    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def restore_backup_file(source_path):
    """يستعيد قاعدة البيانات الحيّة من ملف نسخة احتياطية موجود في source_path
    باستخدام SQLite backup API (نسخ متسق على مستوى الصفحات)."""
    src = sqlite3.connect(source_path)
    try:
        dst = sqlite3.connect(DB_PATH)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def is_valid_sqlite_file(path):
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        return header == b"SQLite format 3\x00"
    except Exception:
        return False


def next_grade_id():
    """يحدد رقم تعريف (GradeID) جديد لصف يُضاف بعد الثاني عشر (13, 14, ...)
    حتى لا نتعارض مع صفوف الروضة (الأرقام السالبة) أو الصفوف 1-12 الحالية."""
    rows = query_db("SELECT GradeID FROM Grades")
    max_id = 12
    for r in rows:
        if r['GradeID'] > max_id:
            max_id = r['GradeID']
    return max_id + 1


def build_parent_name_from_student(full_name):
    """يبني اسم ولي الأمر تلقائياً من الاسم الثاني والثالث والرابع في اسم الطالب
    (أي اسم الأب، الجد، والعائلة)، ويُستبعد الاسم الأول (اسم الطالب نفسه).
    مثال: "احمد اكرم جابر سعدات" -> ولي الأمر: "اكرم جابر سعدات".
    يتطلب اسماً مكوناً من 3 أجزاء على الأقل ليُنتج اسماً منطقياً لولي الأمر."""
    if not full_name:
        return None
    parts = [p for p in str(full_name).strip().split() if p]
    if len(parts) < 3:
        return None
    parent_parts = parts[1:4]  # الاسم الثاني والثالث والرابع (إن وجد)
    parent_name = " ".join(parent_parts).strip()
    return parent_name or None


def find_or_create_parent(parent_name, relation='ولي أمر', mobile=None):
    """يعيد استخدام ولي الأمر حسب رقم الجوال أولاً؛ وإذا لم يوجد جوال يعتمد الاسم.
    هذا يضمن أن ولي الأمر الواحد يمكن ربطه بأكثر من طالب دون إنشاء سجلات مكررة."""
    parent_name = (parent_name or '').strip()
    mobile = (mobile or '').strip() or None
    if not parent_name:
        return None
    if mobile:
        existing = query_db("SELECT ParentID FROM Parents WHERE COALESCE(TRIM(Mobile),'')=? LIMIT 1", (mobile,), one=True)
    else:
        existing = query_db("SELECT ParentID FROM Parents WHERE TRIM(ParentName)=? LIMIT 1", (parent_name,), one=True)
    if existing:
        return existing['ParentID']
    return execute_db("INSERT INTO Parents (ParentName, Relation, Mobile) VALUES (?,?,?)", (parent_name, relation, mobile))


def set_student_parent(student_id, parent_id):
    """يطبّق العلاقة: كل طالب له ولي أمر واحد فقط. يحذف أي ربط سابق لهذا الطالب
    (إن وجد) قبل إضافة الربط الجديد، بينما لا يمس ارتباطات باقي الطلاب بنفس ولي الأمر."""
    execute_db("DELETE FROM ParentStudents WHERE StudentID=?", (student_id,))
    execute_db("INSERT INTO ParentStudents (ParentID, StudentID) VALUES (?,?)", (parent_id, student_id))


def validate_national_id(value):
    """رقم الهوية يجب أن يكون 9 أرقام بالضبط (إن أُدخل). يرجع رسالة خطأ أو None إن كان صحيحاً."""
    if value in (None, ''):
        return None
    digits = str(value).strip()
    if not digits.isdigit() or len(digits) != 9:
        return "رقم الهوية يجب أن يكون 9 أرقام بالضبط"
    return None


def validate_mobile(value, label="رقم الجوال"):
    """رقم الجوال يجب أن يكون 10 أرقام بالضبط (إن أُدخل). يرجع رسالة خطأ أو None إن كان صحيحاً."""
    if value in (None, ''):
        return None
    digits = str(value).strip()
    if not digits.isdigit() or len(digits) != 10:
        return f"{label} يجب أن يكون 10 أرقام بالضبط"
    return None


def validate_student_data(data):
    """يتحقق من صحة بيانات الطالب (رقم الهوية ورقم/أرقام الجوال) قبل الحفظ.
    يرجع قائمة رسائل الأخطاء (فارغة إن كانت البيانات صحيحة)."""
    errors = []
    err = validate_national_id(data.get('NationalID'))
    if err:
        errors.append(err)
    err = validate_mobile(data.get('Mobile1'), "رقم الجوال الأول")
    if err:
        errors.append(err)
    err = validate_mobile(data.get('Mobile2'), "رقم الجوال الثاني")
    if err:
        errors.append(err)
    return errors


# ===== أعمدة قالب استيراد/تصدير الطلاب من Excel =====
# الترتيب هنا هو نفس ترتيب الأعمدة في ملف Excel (يبدأ من العمود A)
STUDENT_IMPORT_COLUMNS = [
    "الاسم الكامل",        # FullName (مطلوب)
    "رقم الهوية",          # NationalID
    "الجنس",               # GenderName -> ذكر / أنثى
    "تاريخ الميلاد",       # BirthDate -> YYYY-MM-DD
    "جوال 1",              # Mobile1
    "جوال 2",              # Mobile2
    "العنوان",             # Address
    "المحافظة",            # GovName -> يجب أن تطابق اسم محافظة موجودة
    "الصف",                # GradeName -> يجب أن يطابق اسم صف موجود
    "الشعبة",              # Section
    "حالة الطالب",         # StatusName -> نشط / متخرج / منسحب / موقوف
    "الرسم الشهري المطلوب",  # MonthlyFee
    "ملاحظات",             # Notes
]


def build_student_import_template():
    """يبني ملف Excel (قالب) فيه ورقة لتعبئة بيانات الطلاب + ورقة قوائم مرجعية
    (الصفوف المتاحة، المحافظات، حالات الطلاب) لمساعدة المستخدم على التعبئة الصحيحة.
    يعيد bytes جاهزة للتنزيل."""
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "الطلاب"
    ws.sheet_view.rightToLeft = True

    header_fill = PatternFill(start_color="1d4ed8", end_color="1d4ed8", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    for col_idx, col_name in enumerate(STUDENT_IMPORT_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[cell.column_letter].width = 20

    # صف مثال لمساعدة المستخدم (اختياري - يمكن حذفه أو تعديله)
    example_row = [
        "محمد احمد خالد عبدالله", "900123456", "ذكر", "2012-05-14",
        "0599123456", "", "نابلس - البلدة القديمة", "نابلس",
        "السابع", "أ", "نشط", "50", ""
    ]
    for col_idx, val in enumerate(example_row, start=1):
        ws.cell(row=2, column=col_idx, value=val)

    # ورقة القوائم المرجعية لمساعدة المستخدم على معرفة القيم الصحيحة المسموحة
    ws2 = wb.create_sheet("قوائم مرجعية - لا تعدل")
    ws2.sheet_view.rightToLeft = True

    grades = query_db("SELECT GradeID, GradeName FROM Grades ORDER BY GradeID")
    govs = query_db("SELECT GovName FROM Governorates ORDER BY GovName")
    statuses = query_db("SELECT StatusName FROM Status ORDER BY StatusID")

    ws2.cell(row=1, column=1, value="أسماء الصفوف المتاحة (انسخ الاسم كما هو بالضبط)").font = Font(bold=True)
    for i, g in enumerate(grades, start=2):
        ws2.cell(row=i, column=1, value=g['GradeName'])

    ws2.cell(row=1, column=2, value="أسماء المحافظات المتاحة").font = Font(bold=True)
    for i, g in enumerate(govs, start=2):
        ws2.cell(row=i, column=2, value=g['GovName'])

    ws2.cell(row=1, column=3, value="حالات الطلاب المتاحة").font = Font(bold=True)
    for i, s in enumerate(statuses, start=2):
        ws2.cell(row=i, column=3, value=s['StatusName'])

    ws2.cell(row=1, column=4, value="الجنس المتاح").font = Font(bold=True)
    ws2.cell(row=2, column=4, value="ذكر")
    ws2.cell(row=3, column=4, value="أنثى")

    for col in ['A', 'B', 'C', 'D']:
        ws2.column_dimensions[col].width = 28

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_students_from_excel(file_bytes):
    """يقرأ ملف Excel (بصيغة القالب أعلاه) ويضيف الطلاب الموجودين فيه دفعة واحدة.
    يتجاوز الصفوف الفاضية أو التي لا تحتوي اسم طالب. يطابق أسماء الصفوف/المحافظات/الحالات
    نصياً (بدون حساسية لحالة الأحرف أو الفراغات الزائدة) ليحصل على الـ ID المطابق.
    يعيد ملخص: عدد المضاف، عدد المتجاوز، وقائمة الأخطاء التفصيلية لكل صف به مشكلة."""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb["الطلاب"] if "الطلاب" in wb.sheetnames else wb.worksheets[0]

    grades = query_db("SELECT GradeID, GradeName FROM Grades")
    grade_map = {str(g['GradeName']).strip(): g['GradeID'] for g in grades}
    govs = query_db("SELECT GovID, GovName FROM Governorates")
    gov_map = {str(g['GovName']).strip(): g['GovID'] for g in govs}
    statuses = query_db("SELECT StatusID, StatusName FROM Status")
    status_map = {str(s['StatusName']).strip(): s['StatusID'] for s in statuses}
    gender_map = {"ذكر": 1, "أنثى": 2}

    added = 0
    skipped = 0
    errors = []

    rows = list(ws.iter_rows(min_row=2, values_only=True))
    for row_idx, row in enumerate(rows, start=2):
        if not row or not any(c is not None and str(c).strip() != '' for c in row):
            continue  # صف فاضي بالكامل، تجاوزه بصمت
        vals = list(row) + [None] * (len(STUDENT_IMPORT_COLUMNS) - len(row))
        full_name = str(vals[0]).strip() if vals[0] else ''
        if not full_name:
            skipped += 1
            errors.append(f"الصف {row_idx}: تم التجاوز - لا يوجد اسم طالب")
            continue

        national_id = str(vals[1]).strip() if vals[1] else None
        gender_name = str(vals[2]).strip() if vals[2] else None
        birth_date = vals[3]
        if birth_date and hasattr(birth_date, 'strftime'):
            birth_date = birth_date.strftime('%Y-%m-%d')
        elif birth_date:
            birth_date = str(birth_date).strip()
        mobile1 = str(vals[4]).strip() if vals[4] else None
        mobile2 = str(vals[5]).strip() if vals[5] else None
        address = str(vals[6]).strip() if vals[6] else None
        gov_name = str(vals[7]).strip() if vals[7] else None
        grade_name = str(vals[8]).strip() if vals[8] else None
        section = str(vals[9]).strip() if vals[9] else 'أ'
        status_name = str(vals[10]).strip() if vals[10] else None
        monthly_fee = vals[11]
        notes = str(vals[12]).strip() if vals[12] else None

        if national_id:
            nid_err = validate_national_id(national_id)
            if nid_err:
                skipped += 1
                errors.append(f"الصف {row_idx} ({full_name}): تم التجاوز - {nid_err}")
                continue
            existing = query_db("SELECT StudentID FROM Students WHERE NationalID=?", (national_id,))
            if existing:
                skipped += 1
                errors.append(f"الصف {row_idx} ({full_name}): تم التجاوز - رقم الهوية مكرر")
                continue

        m1_err = validate_mobile(mobile1, "رقم الجوال الأول")
        if m1_err:
            skipped += 1
            errors.append(f"الصف {row_idx} ({full_name}): تم التجاوز - {m1_err}")
            continue
        m2_err = validate_mobile(mobile2, "رقم الجوال الثاني")
        if m2_err:
            skipped += 1
            errors.append(f"الصف {row_idx} ({full_name}): تم التجاوز - {m2_err}")
            continue

        gender_id = gender_map.get(gender_name) if gender_name else None
        gov_id = gov_map.get(gov_name) if gov_name else None
        status_id = status_map.get(status_name) if status_name else 1
        grade_id = grade_map.get(grade_name) if grade_name else None

        if gender_name and gender_id is None:
            errors.append(f"الصف {row_idx} ({full_name}): تنبيه - قيمة الجنس \"{gender_name}\" غير معروفة، تم تركها فاضية")
        if gov_name and gov_id is None:
            errors.append(f"الصف {row_idx} ({full_name}): تنبيه - المحافظة \"{gov_name}\" غير معروفة، تم تركها فاضية")
        if grade_name and grade_id is None:
            errors.append(f"الصف {row_idx} ({full_name}): تنبيه - اسم الصف \"{grade_name}\" غير معروف، لم يتم تسجيله بصف")
        if status_name and status_id is None:
            status_id = 1
            errors.append(f"الصف {row_idx} ({full_name}): تنبيه - حالة الطالب \"{status_name}\" غير معروفة، تم اعتبارها (نشط)")

        try:
            monthly_fee = float(monthly_fee) if monthly_fee not in (None, '') else 60
        except (TypeError, ValueError):
            monthly_fee = 60

        student_no = generate_student_no()
        sid = execute_db("""
            INSERT INTO Students (StudentNo, NationalID, FullName, GenderID,
                BirthDate, Mobile1, Mobile2, Address, StatusID, GovID, Notes, MonthlyFee)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            student_no, national_id, full_name, gender_id,
            birth_date, mobile1, mobile2, address, status_id,
            gov_id, notes, monthly_fee
        ))

        if grade_id is not None:
            execute_db("""
                INSERT INTO Enrollments (StudentID, GradeID, Section, AcademicYear)
                VALUES (?,?,?,?)
            """, (sid, grade_id, section or 'أ', '2026-2027'))

        auto_parent_name = build_parent_name_from_student(full_name)
        if auto_parent_name:
            # إن كان لأحد الإخوة المستوردين سابقاً نفس ولي الأمر (نفس الاسم والجوال) يُعاد استخدام
            # سجله نفسه بدل تكراره، تطبيقاً لعلاقة: ولي أمر واحد قد يرتبط بأكثر من طالب
            auto_pid = find_or_create_parent(auto_parent_name, 'أب', mobile1)
            set_student_parent(sid, auto_pid)

        ensure_current_monthly_fee(sid)
        added += 1

    return {"added": added, "skipped": skipped, "errors": errors}



def ensure_receipt_payment_method_column():
    # يضمن وجود عمود طريقة الدفع في سندات القبض دون حذف أي بيانات.
    conn = get_connection()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(FeeReceipts)").fetchall()]
        if "PaymentMethod" not in cols:
            conn.execute("ALTER TABLE FeeReceipts ADD COLUMN PaymentMethod TEXT DEFAULT 'كاش'")
            conn.commit()
    finally:
        conn.close()

ensure_receipt_payment_method_column()


# ===== STUDENT_FINANCE_LEDGER_PATCH_V1 SERVER =====
def finance_summary_for_user(user):
    scope, sp = teacher_student_scope_sql(user, 'e') if user and user.get('Role') == 'teacher' else ('', [])
    rows = query_db("""
        SELECT DISTINCT s.StudentID,
          COALESCE((SELECT MAX(COALESCE(mf.Amount,0)-COALESCE(mf.AmountPaid,0),0)
                    FROM MonthlyFees mf WHERE mf.StudentID=s.StudentID
                    ORDER BY mf.Year DESC,mf.Month DESC LIMIT 1),0) AS MonthlyDue,
          COALESCE((SELECT SUM(MAX(COALESCE(f.Amount,0)-COALESCE(f.AmountPaid,0),0))
                    FROM Fees f WHERE f.StudentID=s.StudentID),0) AS ExtraDue,
          COALESCE((SELECT CreditBalance FROM StudentFinanceAccounts a WHERE a.StudentID=s.StudentID),0) AS Credit,
          COALESCE((SELECT SUM(r.Amount) FROM FeeReceipts r WHERE r.StudentID=s.StudentID),0) AS CashPaid,
          COALESCE((SELECT AmountPaid FROM MonthlyFees mm WHERE mm.StudentID=s.StudentID
                    ORDER BY mm.Year DESC,mm.Month DESC LIMIT 1),0) AS CurrentMonthlyPaid
        FROM Students s LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
        WHERE COALESCE(s.IsArchived,0)=0
    """ + scope, sp)
    paid = sum(float(r.get('CashPaid') or 0) for r in rows)
    remaining = sum(max(0.0, float(r.get('MonthlyDue') or 0) + float(r.get('ExtraDue') or 0) - float(r.get('Credit') or 0)) for r in rows)
    unpaid = sum(1 for r in rows if max(0.0, float(r.get('MonthlyDue') or 0) + float(r.get('ExtraDue') or 0) - float(r.get('Credit') or 0)) > 0.009 and float(r.get('CurrentMonthlyPaid') or 0) <= 0.009)
    return {'fees_paid': paid, 'fees_remaining': remaining, 'fees_total': paid + remaining, 'fees_unpaid_count': unpaid}
# ===== /STUDENT_FINANCE_LEDGER_PATCH_V1 SERVER =====

class APIHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def send_security_headers(self, html=False):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        if os.environ.get('AFAQ_CLOUD', '').strip().lower() in ('1', 'true', 'yes', 'on'):
            self.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        if html:
            self.send_header(
                'Content-Security-Policy',
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'"
            )

    def send_json(self, data, status=200):
        # تسجيل كل عمليات الكتابة الناجحة تلقائياً في سجل نشاطات المستخدم
        if status < 400 and self.command in ('POST', 'PUT', 'DELETE') and not getattr(self, '_activity_logged', False):
            req_path = urlparse(self.path).path
            if req_path not in ('/api/auth/login', '/api/auth/logout'):
                activity_user = self.current_user()
                if activity_user:
                    log_activity(
                        activity_user.get('UserID'),
                        describe_api_action(self.command, req_path, data),
                        self.command,
                        req_path,
                        (data.get('message') if isinstance(data, dict) else None),
                        activity_user.get('Username'),
                        activity_user.get('FullName')
                    )
            self._activity_logged = True
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_security_headers()
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_security_headers(html=True)
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_binary(self, data, content_type, filename):
        from urllib.parse import quote
        ascii_fallback = 'download.xlsx' if filename.endswith('.xlsx') else 'download'
        encoded_name = quote(filename)
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Disposition',
                          f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded_name}")
        self.send_security_headers()
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_security_headers()
        self.end_headers()

    def get_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def current_user(self):
        auth = self.headers.get('Authorization', '')
        token = ''
        if auth.startswith('Bearer '):
            token = auth.split(' ', 1)[1].strip()
        if not token:
            qs = parse_qs(urlparse(self.path).query)
            token = qs.get('token', [''])[0]
        if not token:
            return None
        return query_db("""
            SELECT u.UserID, u.Username, u.FullName, u.Role, u.IsActive, u.CanEnterMarks
            FROM UserSessions s JOIN Users u ON s.UserID=u.UserID
            WHERE s.Token=? AND u.IsActive=1
              AND (s.ExpiresAt IS NULL OR datetime(s.ExpiresAt) > datetime('now','localtime'))
        """, (token,), one=True)

    def require_auth(self):
        user = self.current_user()
        if not user:
            self.send_json({"error": "يرجى تسجيل الدخول أولاً"}, 401)
            return None
        return user

    def require_admin(self):
        user = self.require_auth()
        if not user:
            return None
        if user.get('Role') != 'admin':
            self.send_json({"error": "هذه العملية للمدير فقط"}, 403)
            return None
        return user

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        def qp(key, default=None):
            return qs.get(key, [default])[0]

        if path == '/' or path == '/index.html':
            self.send_html(get_main_html())
            return

        if path.startswith('/api/') and path != '/api/health':
            user = self.require_auth()
            if not user:
                return

        if path == '/api/health':
            self.send_json({"status": "ok", "application": "مشروع تخرج حسين أحمد", "edition": "Graduation Edition 1.0"})

        elif path == '/api/auth/me':
            profile = query_db('SELECT * FROM TeacherProfiles WHERE UserID=?', (user['UserID'],), one=True) if user.get('Role') == 'teacher' else None
            allowed = query_db('''SELECT tg.GradeID, tg.Section, gr.GradeName FROM TeacherGrades tg LEFT JOIN Grades gr ON tg.GradeID=gr.GradeID WHERE tg.UserID=? ORDER BY tg.GradeID''', (user['UserID'],))
            self.send_json({**user, 'profile': profile, 'allowedGrades': allowed, 'subjects': teacher_subject_names(user['UserID']) if user.get('Role')=='teacher' else []})

        elif path == '/api/users':
            if user.get('Role') != 'admin':
                self.send_json({"error":"هذه الصفحة للمدير فقط"}, 403); return
            rows = query_db('''
                SELECT u.UserID,u.Username,u.FullName,u.Role,u.IsActive,u.CanEnterMarks,u.LastLogin,u.CreatedAt,
                       tp.NationalID,tp.Phone,tp.SerialNo,tp.Subject,
                       (SELECT GROUP_CONCAT(sub.SubjectName, '، ') FROM TeacherSubjects ts JOIN Subjects sub ON sub.SubjectID=ts.SubjectID WHERE ts.UserID=u.UserID) as SubjectsText,
                       (SELECT '[' || GROUP_CONCAT('{"SubjectID":'||sub.SubjectID||',"SubjectName":"'||REPLACE(sub.SubjectName,'"','')||'"}') || ']' FROM TeacherSubjects ts JOIN Subjects sub ON sub.SubjectID=ts.SubjectID WHERE ts.UserID=u.UserID) as SubjectsJson,
                       GROUP_CONCAT(gr.GradeName || COALESCE(' - شعبة ' || NULLIF(tg.Section,''), ''), '، ') as GradesText,
                       '[' || GROUP_CONCAT(CASE WHEN tg.GradeID IS NOT NULL THEN '{"GradeID":'||tg.GradeID||',"Section":"'||COALESCE(tg.Section,'')||'"}' END) || ']' as GradesJson
                FROM Users u
                LEFT JOIN TeacherProfiles tp ON u.UserID=tp.UserID
                LEFT JOIN TeacherGrades tg ON u.UserID=tg.UserID
                LEFT JOIN Grades gr ON tg.GradeID=gr.GradeID
                GROUP BY u.UserID ORDER BY u.UserID DESC
            ''')
            self.send_json(rows)

        elif path.startswith('/api/users/') and path.endswith('/activities'):
            if user.get('Role') != 'admin':
                self.send_json({"error":"سجل النشاطات للمدير فقط"}, 403); return
            uid = path.split('/')[3]
            limit = qp('limit', '100')
            try:
                limit = max(1, min(500, int(limit)))
            except Exception:
                limit = 100
            rows = query_db("""
                SELECT ActivityID, UserID, Username, FullName, Action, Method, Path, Details, CreatedAt
                FROM ActivityLogs WHERE UserID=?
                ORDER BY ActivityID DESC LIMIT ?
            """, (uid, limit))
            self.send_json(rows)

        elif path == '/api/students/import-template':
            if not OPENPYXL_AVAILABLE:
                self.send_json({"error": "مكتبة openpyxl غير مثبتة على هذا الجهاز. شغّل: pip install openpyxl"}, 500)
                return
            file_bytes = build_student_import_template()
            self.send_binary(
                file_bytes,
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'قالب_استيراد_الطلاب.xlsx'
            )

        elif path == '/api/students':
            search = qp('search', '')
            status = qp('status', '')
            gender = qp('gender', '')
            grade = qp('grade', '')
            gov = qp('gov', '')
            fee_status = qp('fee_status', '')
            school = qp('school', '')
            section = qp('section', '')
            health = qp('health', '')
            social = qp('social', '')
            financial = qp('financial', '')
            archived = qp('archived', '')  # '' = فقط النشطون (الافتراضي)، '1' = فقط المؤرشفون، 'all' = الجميع
            sql = """
                SELECT s.StudentID, s.StudentNo, s.NationalID, s.FullName,
                       g.GenderName, s.BirthDate, s.Mobile1, s.Mobile2,
                       s.Address, st.StatusName, gov.GovName, s.RegDate, s.Notes,
                       s.GenderID, s.StatusID, s.GovID, s.MonthlyFee,
                       s.IsArchived, s.ArchivedDate, s.ArchiveReason, s.ArchiveNote,
                       (COALESCE((SELECT SUM(COALESCE(fx.AmountPaid,0)) FROM Fees fx WHERE fx.StudentID=s.StudentID),0)
                        + COALESCE((SELECT SUM(COALESCE(mx.AmountPaid,0)) FROM MonthlyFees mx WHERE mx.StudentID=s.StudentID),0)) AS FeesPaid,
                       (COALESCE((SELECT SUM(CASE WHEN COALESCE(fx.AmountPaid,0) < COALESCE(fx.Amount,0)
                                                  THEN COALESCE(fx.Amount,0)-COALESCE(fx.AmountPaid,0) ELSE 0 END)
                                  FROM Fees fx WHERE fx.StudentID=s.StudentID),0)
                        + COALESCE((SELECT CASE WHEN COALESCE(mx.AmountPaid,0) < COALESCE(mx.Amount,0)
                                               THEN COALESCE(mx.Amount,0)-COALESCE(mx.AmountPaid,0) ELSE 0 END
                                    FROM MonthlyFees mx WHERE mx.StudentID=s.StudentID
                                    ORDER BY mx.Year DESC, mx.Month DESC, mx.MonthlyFeeID DESC LIMIT 1),0)) AS FeesRemaining,
                       ((SELECT COUNT(*) FROM Fees fx WHERE fx.StudentID=s.StudentID)
                        + (SELECT COUNT(*) FROM MonthlyFees mx WHERE mx.StudentID=s.StudentID)) AS FeeRecordCount,
                       CASE WHEN s.Photo IS NOT NULL AND length(s.Photo)>0 THEN 1 ELSE 0 END as HasPhoto,  -- ARCHIVED_STUDENTS_FEES_PATCH_V1
                       gr.GradeName, e.Section, e.AcademicYear, e.GradeID,
                       sch.SchoolName, e.SchoolID,
                       hs.HealthType, orp.OrphanType, fc.CategoryName as FinancialCategoryName, fst.FinCategoryID
                FROM Students s
                LEFT JOIN Gender g ON s.GenderID = g.GenderID
                LEFT JOIN Status st ON s.StatusID = st.StatusID
                LEFT JOIN Governorates gov ON s.GovID = gov.GovID
                LEFT JOIN Enrollments e ON s.StudentID = e.StudentID
                LEFT JOIN Grades gr ON e.GradeID = gr.GradeID
                LEFT JOIN Schools sch ON e.SchoolID = sch.SchoolID
                LEFT JOIN Orphans orp ON orp.StudentID = s.StudentID
                LEFT JOIN HealthStatus hs ON hs.StudentID = s.StudentID
                LEFT JOIN FinancialStatus fst ON fst.StudentID = s.StudentID
                LEFT JOIN FinancialCategories fc ON fc.FinCategoryID = fst.FinCategoryID
                LEFT JOIN (
                    SELECT ps.StudentID, GROUP_CONCAT(p.ParentName, ' | ') as ParentNames
                    FROM ParentStudents ps JOIN Parents p ON p.ParentID = ps.ParentID
                    GROUP BY ps.StudentID
                ) par ON par.StudentID = s.StudentID
                WHERE 1=1
            """
            params = []
            if search:
                search_fields = """(
                    s.FullName LIKE ? OR s.StudentNo LIKE ? OR s.NationalID LIKE ?
                    OR s.Mobile1 LIKE ? OR s.Mobile2 LIKE ?
                    OR par.ParentNames LIKE ?
                    OR sch.SchoolName LIKE ?
                    OR gr.GradeName LIKE ?
                    OR g.GenderName LIKE ?
                    OR orp.OrphanType LIKE ?
                    OR st.StatusName LIKE ?
                )"""
                words = [w for w in search.split() if w.strip()]
                if not words:
                    words = [search]
                for w in words:
                    like = f"%{w}%"
                    sql += " AND " + search_fields
                    params += [like] * 11
            if status:
                sql += " AND s.StatusID = ?"
                params.append(status)
            if gender:
                sql += " AND s.GenderID = ?"
                params.append(gender)
            if grade:
                sql += " AND (e.GradeID = ? OR EXISTS (SELECT 1 FROM StudentClassMemberships scm WHERE scm.StudentID=s.StudentID AND scm.GradeID=?))"
                params += [grade, grade]
            if gov:
                sql += " AND s.GovID = ?"
                params.append(gov)
            if school:
                sql += " AND e.SchoolID = ?"
                params.append(school)
            if section:
                sql += " AND e.Section = ?"
                params.append(section)
            if health:
                if health == '__none__':
                    sql += " AND (hs.HealthType IS NULL OR hs.HealthType = '' OR hs.HealthType = 'سليم')"
                else:
                    sql += " AND hs.HealthType = ?"
                    params.append(health)
            if social:
                if social == '__none__':
                    sql += " AND (orp.OrphanType IS NULL OR orp.OrphanType = '')"
                else:
                    sql += " AND orp.OrphanType = ?"
                    params.append(social)
            if financial:
                sql += " AND fst.FinCategoryID = ?"
                params.append(financial)

            # STUDENT_PAYMENT_STATUS_FILTER_PATCH_V1
            # التصنيف يعتمد على الرصيد الحقيقي الحالي للطالب، وليس مجموع دفعات الأشهر القديمة:
            # - الرسم الشهري: آخر شهر فقط (لأن المتأخرات مرحّلة إليه)
            # - الرسوم الإضافية: كل المتبقي غير المسدد
            # - رصيد الطالب الدائن يُخصم من المطلوب
            if fee_status:
                fee_record_count = """(
                    (SELECT COUNT(*) FROM MonthlyFees mf0 WHERE mf0.StudentID=s.StudentID)
                    + (SELECT COUNT(*) FROM Fees f0 WHERE f0.StudentID=s.StudentID)
                )"""
                monthly_due = """COALESCE((
                    SELECT MAX(COALESCE(mf.Amount,0)-COALESCE(mf.AmountPaid,0),0)
                    FROM MonthlyFees mf
                    WHERE mf.StudentID=s.StudentID
                    ORDER BY mf.Year DESC, mf.Month DESC, mf.MonthlyFeeID DESC
                    LIMIT 1
                ),0)"""
                extra_due = """COALESCE((
                    SELECT SUM(MAX(COALESCE(f.Amount,0)-COALESCE(f.AmountPaid,0),0))
                    FROM Fees f WHERE f.StudentID=s.StudentID
                ),0)"""
                credit = """COALESCE((
                    SELECT a.CreditBalance FROM StudentFinanceAccounts a
                    WHERE a.StudentID=s.StudentID
                ),0)"""
                monthly_paid = """COALESCE((
                    SELECT COALESCE(mfp.AmountPaid,0)
                    FROM MonthlyFees mfp
                    WHERE mfp.StudentID=s.StudentID
                    ORDER BY mfp.Year DESC, mfp.Month DESC, mfp.MonthlyFeeID DESC
                    LIMIT 1
                ),0)"""
                extra_paid_toward_open = """COALESCE((
                    SELECT SUM(CASE
                        WHEN COALESCE(fp.Amount,0) > COALESCE(fp.AmountPaid,0)
                        THEN MAX(COALESCE(fp.AmountPaid,0),0)
                        ELSE 0 END)
                    FROM Fees fp WHERE fp.StudentID=s.StudentID
                ),0)"""
                net_due = f"(({monthly_due}) + ({extra_due}) - ({credit}))"
                payment_progress = f"(({monthly_paid}) + ({extra_paid_toward_open}) + ({credit}))"

                if fee_status == 'paid':
                    sql += f" AND {fee_record_count} > 0 AND {net_due} <= 0.009"
                elif fee_status == 'partial':
                    sql += f" AND {fee_record_count} > 0 AND {net_due} > 0.009 AND {payment_progress} > 0.009"
                elif fee_status == 'unpaid':
                    sql += f" AND {fee_record_count} > 0 AND {net_due} > 0.009 AND {payment_progress} <= 0.009"

            if archived == '1':
                sql += " AND s.IsArchived = 1"
            elif archived == 'all':
                pass
            else:
                sql += " AND s.IsArchived = 0"
            extra, params = teacher_grade_filter(user, params)
            sql += extra
            sql += " ORDER BY s.StudentID DESC"
            self.send_json(query_db(sql, params))

        elif path == '/api/students/new_no':
            self.send_json({"no": generate_student_no()})

        elif path.startswith('/api/students/') and path.endswith('/parents'):
            # كل طالب له ولي أمر واحد فقط، لذا سترجع هذه القائمة عنصراً واحداً كحد أقصى
            sid = path.split('/')[3]
            rows = query_db("""
                SELECT p.* FROM Parents p
                JOIN ParentStudents ps ON ps.ParentID = p.ParentID
                WHERE ps.StudentID = ?
            """, (sid,))
            self.send_json(rows)

        elif path.startswith('/api/students/') and path.endswith('/siblings'):
            # الطلاب الآخرون الذين يشتركون بنفس ولي الأمر مع هذا الطالب
            sid = path.split('/')[3]
            rows = query_db("""
                SELECT s2.StudentID, s2.FullName, s2.StudentNo, gr.GradeName
                FROM ParentStudents ps1
                JOIN ParentStudents ps2 ON ps2.ParentID = ps1.ParentID AND ps2.StudentID <> ps1.StudentID
                JOIN Students s2 ON s2.StudentID = ps2.StudentID
                LEFT JOIN Enrollments e ON e.StudentID = s2.StudentID
                LEFT JOIN Grades gr ON gr.GradeID = e.GradeID
                WHERE ps1.StudentID = ?
                ORDER BY s2.FullName
            """, (sid,))
            self.send_json(rows)

        elif path == '/api/parents/search':
            q = qp('q', '').strip()
            if not q:
                self.send_json([])
            else:
                like = f"%{q}%"
                rows = query_db("""
                    SELECT p.*, (SELECT COUNT(*) FROM ParentStudents ps WHERE ps.ParentID = p.ParentID) as StudentsCount
                    FROM Parents p
                    WHERE p.ParentName LIKE ? OR p.Mobile LIKE ? OR p.NationalID LIKE ?
                    ORDER BY p.ParentName LIMIT 20
                """, (like, like, like))
                self.send_json(rows)

        elif path.startswith('/api/parents/') and path.endswith('/students'):
            pid = path.split('/')[3]
            rows = query_db("""
                SELECT s.StudentID, s.FullName, s.StudentNo, gr.GradeName, e.Section
                FROM ParentStudents ps
                JOIN Students s ON s.StudentID = ps.StudentID
                LEFT JOIN Enrollments e ON e.StudentID = s.StudentID
                LEFT JOIN Grades gr ON gr.GradeID = e.GradeID
                WHERE ps.ParentID = ?
                ORDER BY s.FullName
            """, (pid,))
            self.send_json(rows)

        elif path == '/api/parents':
            # قائمة كل أولياء الأمور (سجل واحد لكل ولي أمر حتى لو كان له عدة طلاب)
            rows = query_db("""
                SELECT p.*,
                       (SELECT COUNT(*) FROM ParentStudents ps WHERE ps.ParentID = p.ParentID) as StudentsCount,
                       (SELECT GROUP_CONCAT(s.FullName, ' | ') FROM ParentStudents ps
                            JOIN Students s ON s.StudentID = ps.StudentID
                            WHERE ps.ParentID = p.ParentID) as StudentNames
                FROM Parents p
                ORDER BY p.ParentName
            """)
            self.send_json(rows)

        elif path.startswith('/api/students/') and path.endswith('/orphan'):
            sid = path.split('/')[3]
            row = query_db("SELECT * FROM Orphans WHERE StudentID=?", (sid,), one=True)
            self.send_json(row or {})

        elif path.startswith('/api/students/') and path.endswith('/health-status'):
            sid = path.split('/')[3]
            row = query_db("SELECT * FROM HealthStatus WHERE StudentID=?", (sid,), one=True)
            self.send_json(row or {})

        elif path.startswith('/api/students/') and path.endswith('/financial-status'):
            sid = path.split('/')[3]
            row = query_db("""
                SELECT fs.*, fc.CategoryName
                FROM FinancialStatus fs
                LEFT JOIN FinancialCategories fc ON fs.FinCategoryID = fc.FinCategoryID
                WHERE fs.StudentID=?
            """, (sid,), one=True)
            self.send_json(row or {})

        elif path.startswith('/api/students/') and path.endswith('/full-status'):
            # الحالة الصحية + الاجتماعية + المادية للطالب في طلب واحد (يُستخدم في القائمة المنبثقة)
            sid = path.split('/')[3]
            health = query_db("SELECT * FROM HealthStatus WHERE StudentID=?", (sid,), one=True) or {}
            social = query_db("SELECT * FROM Orphans WHERE StudentID=?", (sid,), one=True) or {}
            financial = query_db("""
                SELECT fs.*, fc.CategoryName
                FROM FinancialStatus fs
                LEFT JOIN FinancialCategories fc ON fs.FinCategoryID = fc.FinCategoryID
                WHERE fs.StudentID=?
            """, (sid,), one=True) or {}
            self.send_json({"health": health, "social": social, "financial": financial})

        elif path.startswith('/api/students/') and path.endswith('/finance-account'):
            sid = path.split('/')[3]
            if not student_allowed_for_user(user, sid):
                self.send_json({'error':'لا تملك صلاحية الاطلاع على الملف المالي لهذا الطالب'},403); return
            self.send_json(finance_account_snapshot(sid))

        elif path.startswith('/api/students/') and path.endswith('/finance-ledger'):
            sid = path.split('/')[3]
            if not student_allowed_for_user(user, sid):
                self.send_json({'error':'لا تملك صلاحية الاطلاع على الملف المالي لهذا الطالب'},403); return
            self.send_json(finance_ledger_rows(sid, 150))

        elif path == '/api/finance/fee-context':
            kind = qp('kind','monthly')
            fid = qp('id','')
            table = 'MonthlyFees' if kind == 'monthly' else 'Fees'
            idcol = 'MonthlyFeeID' if kind == 'monthly' else 'FeeID'
            row = query_db(f"SELECT StudentID,Amount,AmountPaid FROM {table} WHERE {idcol}=?", (fid,), one=True)
            if not row:
                self.send_json({'error':'الرسم غير موجود'},404); return
            self.send_json({
                'StudentID': row['StudentID'],
                'CreditBalance': finance_get_credit(row['StudentID']),
                'Remaining': max(0,float(row.get('Amount') or 0)-float(row.get('AmountPaid') or 0))
            })

        elif path == '/api/lookup/financial-categories':
            self.send_json(query_db("SELECT * FROM FinancialCategories ORDER BY FinCategoryID"))

        elif path.startswith('/api/students/') and path.endswith('/fees'):
            sid = path.split('/')[3]
            rows = query_db("SELECT * FROM Fees WHERE StudentID=? ORDER BY FeeID DESC", (sid,))
            self.send_json(rows)

        elif path.startswith('/api/students/') and path.endswith('/monthly-fees'):
            sid = path.split('/')[3]
            rows = query_db("""
                SELECT * FROM MonthlyFees WHERE StudentID=?
                ORDER BY Year DESC, Month DESC
            """, (sid,))
            self.send_json(rows)

        elif path.startswith('/api/students/') and path.endswith('/enrollment'):
            sid = path.split('/')[3]
            row = query_db("""
                SELECT e.*, gr.GradeName
                FROM Enrollments e
                LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                WHERE e.StudentID=?
                ORDER BY e.EnrollID DESC LIMIT 1
            """, (sid,), one=True)
            self.send_json(row or {})

        elif path.startswith('/api/students/') and path.endswith('/graduation-project'):
            sid = path.split('/')[3]
            if not student_allowed_for_user(user, sid):
                self.send_json({"error":"لا تملك صلاحية عرض مشروع هذا الطالب"}, 403); return
            row = query_db("SELECT * FROM GraduationProjects WHERE StudentID=?", (sid,), one=True)
            self.send_json(row or {"StudentID": int(sid), "ProjectStatus": "لم يبدأ"})

        elif path.startswith('/api/students/'):
            sid = path.split('/')[3]
            if not student_allowed_for_user(user, sid):
                self.send_json({"error":"لا تملك صلاحية عرض هذا الطالب"}, 403)
                return
            row = query_db("""
                SELECT s.*, CASE WHEN s.Photo IS NOT NULL AND length(s.Photo)>0 THEN 1 ELSE 0 END as HasPhoto, g.GenderName, st.StatusName, gov.GovName,
                       gr.GradeName, e.Section, e.AcademicYear, e.GradeID
                FROM Students s
                LEFT JOIN Gender g ON s.GenderID=g.GenderID
                LEFT JOIN Status st ON s.StatusID=st.StatusID
                LEFT JOIN Governorates gov ON s.GovID=gov.GovID
                LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
                LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                WHERE s.StudentID=?
            """, (sid,), one=True)
            self.send_json(row or {})

        elif path == '/api/lookup/genders':
            self.send_json(query_db("SELECT * FROM Gender"))
        elif path == '/api/lookup/statuses':
            self.send_json(query_db("SELECT * FROM Status"))
        elif path == '/api/lookup/grades':
            self.send_json(query_db("SELECT * FROM Grades ORDER BY GradeID"))
        elif path == '/api/lookup/schools':
            self.send_json(query_db("SELECT SchoolID, SchoolName FROM Schools ORDER BY SchoolName"))
        elif path == '/api/lookup/sections':
            self.send_json(query_db("""
                SELECT DISTINCT Section FROM (
                    SELECT Section FROM Enrollments
                    UNION ALL
                    SELECT Section FROM StudentClassMemberships
                )
                WHERE Section IS NOT NULL AND TRIM(Section) != ''
                ORDER BY Section
            """))
        elif path == '/api/grade-subjects':
            gid = qp('grade', '')
            if gid:
                rows = query_db('''
                    SELECT gs.GradeID, g.GradeName, sub.SubjectID, sub.SubjectName
                    FROM GradeSubjects gs
                    JOIN Subjects sub ON sub.SubjectID=gs.SubjectID
                    LEFT JOIN Grades g ON g.GradeID=gs.GradeID
                    WHERE gs.GradeID=? ORDER BY sub.SubjectName
                ''', (gid,))
            else:
                rows = query_db('''
                    SELECT gs.GradeID, g.GradeName, GROUP_CONCAT(sub.SubjectName, '، ') as SubjectsText,
                           '[' || GROUP_CONCAT('{"SubjectID":'||sub.SubjectID||',"SubjectName":"'||REPLACE(sub.SubjectName,'"','')||'"}') || ']' as SubjectsJson
                    FROM GradeSubjects gs
                    JOIN Subjects sub ON sub.SubjectID=gs.SubjectID
                    LEFT JOIN Grades g ON g.GradeID=gs.GradeID
                    GROUP BY gs.GradeID ORDER BY gs.GradeID
                ''')
            self.send_json(rows)

        elif path == '/api/subjects':
            if user.get('Role') == 'teacher':
                self.send_json(query_db("""
                    SELECT sub.* FROM TeacherSubjects ts
                    JOIN Subjects sub ON sub.SubjectID=ts.SubjectID
                    WHERE ts.UserID=? ORDER BY sub.SubjectName
                """, (user['UserID'],)))
            else:
                self.send_json(query_db("SELECT * FROM Subjects ORDER BY SubjectName"))
        elif path == '/api/grades/manage':
            rows = query_db("""
                SELECT gr.GradeID, gr.GradeName,
                       COUNT(DISTINCT e.StudentID) as StudentCount
                FROM Grades gr
                LEFT JOIN (
                    SELECT StudentID, GradeID FROM Enrollments
                    UNION ALL
                    SELECT StudentID, GradeID FROM StudentClassMemberships
                ) e ON gr.GradeID = e.GradeID
                GROUP BY gr.GradeID
                ORDER BY gr.GradeID
            """)
            self.send_json(rows)
        elif path == '/api/lookup/governorates':
            self.send_json(query_db("SELECT * FROM Governorates ORDER BY GovName"))

        elif path == '/api/grades/students':
            grade = qp('grade', '')
            sql = """
                WITH all_classes AS (
                    SELECT StudentID, GradeID, Section, AcademicYear, 'أساسي' AS MembershipType
                    FROM Enrollments
                    UNION ALL
                    SELECT StudentID, GradeID, Section, AcademicYear, 'إضافي' AS MembershipType
                    FROM StudentClassMemberships
                )
                SELECT DISTINCT s.StudentID, s.StudentNo, s.FullName, g.GenderName,
                       gr.GradeName, e.Section, e.AcademicYear, e.MembershipType,
                       COALESCE((SELECT mf.Amount FROM MonthlyFees mf WHERE mf.StudentID=s.StudentID
                                 ORDER BY mf.Year DESC,mf.Month DESC,mf.MonthlyFeeID DESC LIMIT 1),0) as FeeAmount,
                       COALESCE((SELECT mf.AmountPaid FROM MonthlyFees mf WHERE mf.StudentID=s.StudentID
                                 ORDER BY mf.Year DESC,mf.Month DESC,mf.MonthlyFeeID DESC LIMIT 1),0) as AmountPaid,
                       CASE WHEN COALESCE((SELECT mf.AmountPaid FROM MonthlyFees mf WHERE mf.StudentID=s.StudentID
                                           ORDER BY mf.Year DESC,mf.Month DESC,mf.MonthlyFeeID DESC LIMIT 1),0)
                                      >= COALESCE((SELECT mf.Amount FROM MonthlyFees mf WHERE mf.StudentID=s.StudentID
                                                   ORDER BY mf.Year DESC,mf.Month DESC,mf.MonthlyFeeID DESC LIMIT 1),0)
                            THEN 'مسدد' ELSE 'غير مسدد' END as FeeStatus
                FROM Students s
                LEFT JOIN Gender g ON s.GenderID=g.GenderID
                LEFT JOIN all_classes e ON s.StudentID=e.StudentID
                LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                WHERE COALESCE(s.IsArchived,0)=0
            """
            params = []
            if grade:
                sql += " AND e.GradeID = ?"
                params.append(grade)
            extra, params = teacher_grade_filter(user, params)
            sql += extra
            sql += " ORDER BY gr.GradeID, s.FullName"
            self.send_json(query_db(sql, params))

        elif path == '/api/fees':
            search = qp('search', '')
            paid = qp('paid', '')
            grade = qp('grade', '')
            sql = """
                SELECT f.*, s.FullName, s.StudentNo, gr.GradeName, e.Section
                FROM Fees f
                JOIN Students s ON f.StudentID=s.StudentID
                LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
                LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                WHERE COALESCE(s.IsArchived,0)=0
            """
            params = []
            if search:
                sql += " AND (s.FullName LIKE ? OR s.StudentNo LIKE ?)"
                like = f"%{search}%"
                params += [like, like]
            if paid == '1':
                sql += " AND COALESCE(f.AmountPaid,0) >= COALESCE(f.Amount,0)"
            elif paid == 'partial':
                sql += " AND COALESCE(f.AmountPaid,0) > 0 AND COALESCE(f.AmountPaid,0) < COALESCE(f.Amount,0)"
            elif paid == '0':
                sql += " AND COALESCE(f.AmountPaid,0) <= 0 AND COALESCE(f.Amount,0) > 0"  # PARTIAL_PAYMENT_LIST_PATCH_V1
            if grade:
                sql += " AND e.GradeID = ?"
                params.append(grade)
            sql += " ORDER BY f.FeeID DESC"
            self.send_json(query_db(sql, params))

        elif path == '/api/fees/unpaid':
            grade = qp('grade', '')
            search = qp('search', '')
            sql = """
                SELECT s.StudentID, s.StudentNo, s.FullName, g.GenderName,
                       gr.GradeName, e.Section,
                       f.Amount, f.AmountPaid,
                       (f.Amount - COALESCE(f.AmountPaid,0)) as Remaining,
                       f.DueDate, f.AcademicYear, f.FeeID
                FROM Fees f
                JOIN Students s ON f.StudentID=s.StudentID
                LEFT JOIN Gender g ON s.GenderID=g.GenderID
                LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
                LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                WHERE COALESCE(s.IsArchived,0)=0
                  AND COALESCE(f.AmountPaid,0) <= 0 AND COALESCE(f.Amount,0) > 0
            """
            params = []
            if grade:
                sql += " AND e.GradeID = ?"
                params.append(grade)
            if search:
                sql += " AND (s.FullName LIKE ? OR s.StudentNo LIKE ?)"
                like = f"%{search}%"
                params += [like, like]
            sql += " ORDER BY gr.GradeID, s.FullName"
            self.send_json(query_db(sql, params))

        elif path == '/api/monthly-fees':
            search = qp('search', '')
            paid = qp('paid', '')
            grade = qp('grade', '')
            year = qp('year', '')
            month = qp('month', '')
            sql = """
                SELECT mf.*, s.FullName, s.StudentNo, gr.GradeName, e.Section
                FROM MonthlyFees mf
                JOIN Students s ON mf.StudentID=s.StudentID
                LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
                LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                WHERE COALESCE(s.IsArchived,0)=0
            """
            params = []
            if search:
                sql += " AND (s.FullName LIKE ? OR s.StudentNo LIKE ?)"
                like = f"%{search}%"
                params += [like, like]
            if paid == '1':
                sql += " AND COALESCE(mf.AmountPaid,0) >= COALESCE(mf.Amount,0)"
            elif paid == 'partial':
                sql += " AND COALESCE(mf.AmountPaid,0) > 0 AND COALESCE(mf.AmountPaid,0) < COALESCE(mf.Amount,0)"
            elif paid == '0':
                sql += " AND COALESCE(mf.AmountPaid,0) <= 0 AND COALESCE(mf.Amount,0) > 0"  # PARTIAL_PAYMENT_LIST_PATCH_V1
            if grade:
                sql += " AND e.GradeID = ?"
                params.append(grade)
            if year:
                sql += " AND mf.Year = ?"
                params.append(year)
            if month:
                sql += " AND mf.Month = ?"
                params.append(month)
            sql += " ORDER BY mf.Year DESC, mf.Month DESC, mf.MonthlyFeeID DESC"
            self.send_json(query_db(sql, params))

        elif path == '/api/monthly-fees/unpaid':
            grade = qp('grade', '')
            search = qp('search', '')
            # UNPAID_PRINT_PHONE_PATCH_V2: fixed SQL + contact mobile
            sql = """
                SELECT s.StudentID, s.StudentNo, s.FullName,
                       s.Mobile1, s.Mobile2,
                       COALESCE(
                         NULLIF(TRIM(s.Mobile1),''),
                         NULLIF(TRIM(s.Mobile2),''),
                         (SELECT NULLIF(TRIM(p.Mobile),'')
                            FROM ParentStudents ps
                            JOIN Parents p ON p.ParentID=ps.ParentID
                           WHERE ps.StudentID=s.StudentID
                             AND COALESCE(TRIM(p.Mobile),'')<>''
                           ORDER BY p.ParentID
                           LIMIT 1),
                         ''
                       ) AS ContactMobile,
                       g.GenderName,
                       gr.GradeName, e.Section,
                       mf.Amount, mf.AmountPaid,
                       (mf.Amount - COALESCE(mf.AmountPaid,0)) as Remaining,
                       mf.Year, mf.Month, mf.MonthlyFeeID
                FROM MonthlyFees mf
                JOIN Students s ON mf.StudentID=s.StudentID
                LEFT JOIN Gender g ON s.GenderID=g.GenderID
                LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
                LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                WHERE COALESCE(s.IsArchived,0)=0
                  AND COALESCE(mf.AmountPaid,0) <= 0 AND COALESCE(mf.Amount,0) > 0
                  AND mf.Year = (SELECT MAX(Year) FROM MonthlyFees WHERE StudentID = mf.StudentID)
                  AND mf.Month = (SELECT MAX(Month) FROM MonthlyFees WHERE StudentID = mf.StudentID AND Year = mf.Year)
            """
            params = []
            if grade:
                sql += " AND e.GradeID = ?"
                params.append(grade)
            if search:
                sql += " AND (s.FullName LIKE ? OR s.StudentNo LIKE ?)"
                like = f"%{search}%"
                params += [like, like]
            sql += " ORDER BY gr.GradeID, s.FullName"
            self.send_json(query_db(sql, params))

        elif path.startswith('/api/fees/') and path.endswith('/receipts'):
            fid = path.split('/')[3]
            rows = query_db("""
                SELECT r.*, s.FullName, s.StudentNo, f.Photo as FeePhoto, f.FeeType
                FROM FeeReceipts r
                JOIN Students s ON r.StudentID=s.StudentID
                JOIN Fees f ON r.FeeID=f.FeeID
                WHERE r.FeeID=? AND r.FeeKind='extra'
                ORDER BY r.ReceiptID DESC
            """, (fid,))
            self.send_json(rows)

        elif path.startswith('/api/monthly-fees/') and path.endswith('/receipts'):
            fid = path.split('/')[3]
            rows = query_db("""
                SELECT r.*, s.FullName, s.StudentNo, mf.Photo as FeePhoto
                FROM FeeReceipts r
                JOIN Students s ON r.StudentID=s.StudentID
                JOIN MonthlyFees mf ON r.FeeID=mf.MonthlyFeeID
                WHERE r.FeeID=? AND r.FeeKind='monthly'
                ORDER BY r.ReceiptID DESC
            """, (fid,))
            self.send_json(rows)

        elif path == '/api/receipts/latest':
            sid = qp('student', '')
            if sid:
                row = query_db("""
                    SELECT r.*, s.FullName, s.StudentNo,
                           COALESCE(f.FeeType, 'رسوم شهرية') as FeeType,
                           COALESCE(f.Amount, mf.Amount) as TotalAmount,
                           COALESCE(f.Photo, mf.Photo) as FeePhoto,
                           gr.GradeName, e.Section, e.AcademicYear
                    FROM FeeReceipts r
                    LEFT JOIN Fees f ON r.FeeID=f.FeeID AND r.FeeKind='extra'
                    LEFT JOIN MonthlyFees mf ON r.FeeID=mf.MonthlyFeeID AND r.FeeKind='monthly'
                    JOIN Students s ON r.StudentID=s.StudentID
                    LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
                    LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                    WHERE r.StudentID=? AND COALESCE(r.IsPrinted,0)=1
                    ORDER BY r.ReceiptID DESC LIMIT 1
                """, (sid,), one=True)
                self.send_json(row or {})
            else:
                self.send_json({})

        elif path == '/api/teacher/dashboard':
            if user.get('Role') != 'teacher':
                self.send_json({"error":"هذه اللوحة للمعلم فقط"}, 403); return
            scope, sp = teacher_student_scope_sql(user, 'e')
            total = query_db("SELECT COUNT(DISTINCT s.StudentID) as c FROM Students s LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE COALESCE(s.IsArchived,0)=0" + scope, sp, one=True)['c']
            # حالة السداد تُحسب الآن من الرسوم الشهرية (MonthlyFees) بصفتها النظام الأساسي للرسوم
            unpaid = query_db("""
                SELECT COUNT(DISTINCT s.StudentID) as c FROM Students s
                LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
                JOIN MonthlyFees mf ON s.StudentID=mf.StudentID
                WHERE COALESCE(s.IsArchived,0)=0""" + scope + " AND COALESCE(mf.AmountPaid,0) < COALESCE(mf.Amount,0)", sp, one=True)['c']
            paid = total - unpaid
            allowed = query_db("SELECT tg.GradeID, tg.Section, gr.GradeName FROM TeacherGrades tg LEFT JOIN Grades gr ON tg.GradeID=gr.GradeID WHERE tg.UserID=? ORDER BY tg.GradeID", (user['UserID'],))
            self.send_json({"total":total,"paid":paid,"unpaid":unpaid,"allowedGrades":allowed})

        elif path.startswith('/api/students/') and path.endswith('/photo'):
            sid = path.split('/')[3]
            if not student_allowed_for_user(user, sid):
                self.send_json({"error":"لا تملك صلاحية عرض صورة هذا الطالب"}, 403); return
            row = query_db("SELECT Photo FROM Students WHERE StudentID=?", (sid,), one=True)
            self.send_json({"Photo": (row or {}).get('Photo')})

        elif path == '/api/marks':
            sid = qp('student', '')
            params = []
            sql = """
                SELECT m.*, s.FullName, s.StudentNo, u.FullName as TeacherName,
                       gr.GradeName, e.Section
                FROM StudentMarks m
                JOIN Students s ON m.StudentID=s.StudentID
                LEFT JOIN Users u ON m.TeacherUserID=u.UserID
                LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
                LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                WHERE 1=1
            """
            if sid:
                sql += ' AND m.StudentID=?'; params.append(sid)
            grade_filter = qp('grade', '')
            subject_filter = qp('subject', '')
            if grade_filter:
                sql += ' AND e.GradeID=?'; params.append(grade_filter)
            if subject_filter:
                sql += ' AND m.Subject=?'; params.append(subject_filter)
            if user.get('Role') == 'teacher':
                extra, extra_params = teacher_student_scope_sql(user, 'e')
                sql += extra; params += extra_params
                allowed_subjects = teacher_subject_names(user['UserID'])
                if allowed_subjects:
                    sql += ' AND m.Subject IN (%s)' % ','.join(['?']*len(allowed_subjects)); params += allowed_subjects
                else:
                    sql += ' AND 1=0'
            sql += ' ORDER BY s.FullName, m.Subject'
            self.send_json(query_db(sql, params))

        elif path == '/api/attendance':
            sid = qp('student', '')
            date_ = qp('date', '')
            grade_filter = qp('grade', '')
            section_filter = qp('section', '')
            params = []
            sql = """
                SELECT a.*, s.FullName, s.StudentNo, u.FullName as RecordedByName,
                       gr.GradeName, e.Section, e.GradeID
                FROM Attendance a
                JOIN Students s ON a.StudentID=s.StudentID
                LEFT JOIN Users u ON a.RecordedBy=u.UserID
                LEFT JOIN Enrollments e ON s.StudentID=e.StudentID
                LEFT JOIN Grades gr ON e.GradeID=gr.GradeID
                WHERE 1=1
            """
            if sid:
                sql += ' AND a.StudentID=?'; params.append(sid)
            if date_:
                sql += ' AND a.AttendanceDate=?'; params.append(date_)
            if grade_filter:
                sql += ' AND e.GradeID=?'; params.append(grade_filter)
            if section_filter:
                sql += ' AND e.Section=?'; params.append(section_filter)
            if user.get('Role') == 'teacher':
                extra, extra_params = teacher_student_scope_sql(user, 'e')
                sql += extra; params += extra_params
            sql += ' ORDER BY s.FullName'
            self.send_json(query_db(sql, params))

        elif path == '/api/attendance/summary':
            sid = qp('student', '')
            if not sid:
                self.send_json({"error": "اختر الطالب أولاً"}, 400); return
            if not student_allowed_for_user(user, sid):
                self.send_json({"error": "لا تملك صلاحية الاطلاع على حضور هذا الطالب"}, 403); return
            rows = query_db("""
                SELECT Status, COUNT(*) as c FROM Attendance
                WHERE StudentID=? GROUP BY Status
            """, (sid,))
            recent = query_db("""
                SELECT AttendanceDate, Status, Notes FROM Attendance
                WHERE StudentID=? ORDER BY AttendanceDate DESC LIMIT 30
            """, (sid,))
            self.send_json({"counts": rows, "recent": recent})

        elif path == '/api/reports/summary':
            if user.get('Role') == 'teacher':
                scope, sp = teacher_student_scope_sql(user, 'e')
                total = query_db("SELECT COUNT(DISTINCT s.StudentID) as c FROM Students s LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE COALESCE(s.IsArchived,0)=0" + scope, sp, one=True)['c']
                active = query_db("SELECT COUNT(DISTINCT s.StudentID) as c FROM Students s LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE s.StatusID=1 AND COALESCE(s.IsArchived,0)=0" + scope, sp, one=True)['c']
                orphans = query_db("SELECT COUNT(DISTINCT o.StudentID) as c FROM Orphans o JOIN Students s ON o.StudentID=s.StudentID LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE 1=1" + scope, sp, one=True)['c']
                fees_total = query_db("SELECT COALESCE(SUM(f.Amount),0) as s FROM Fees f JOIN Students s ON f.StudentID=s.StudentID LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE COALESCE(s.IsArchived,0)=0" + scope, sp, one=True)['s']
                fees_paid = query_db("SELECT COALESCE(SUM(f.AmountPaid),0) as s FROM Fees f JOIN Students s ON f.StudentID=s.StudentID LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE COALESCE(s.IsArchived,0)=0" + scope, sp, one=True)['s']
                fees_unpaid_count = query_db("SELECT COUNT(DISTINCT f.StudentID) as c FROM Fees f JOIN Students s ON f.StudentID=s.StudentID LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE COALESCE(s.IsArchived,0)=0 AND COALESCE(f.AmountPaid,0) <= 0 AND COALESCE(f.Amount,0) > 0" + scope, sp, one=True)['c']
                mscope, msp = teacher_student_scope_sql(user, 'e')
                mfees_total = query_db("SELECT COALESCE(SUM(mf.Amount),0) as s FROM MonthlyFees mf JOIN Students s ON mf.StudentID=s.StudentID LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE COALESCE(s.IsArchived,0)=0" + mscope, msp, one=True)['s']
                mfees_paid = query_db("SELECT COALESCE(SUM(mf.AmountPaid),0) as s FROM MonthlyFees mf JOIN Students s ON mf.StudentID=s.StudentID LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE COALESCE(s.IsArchived,0)=0" + mscope, msp, one=True)['s']
                mfees_unpaid_count = query_db("SELECT COUNT(DISTINCT mf.StudentID) as c FROM MonthlyFees mf JOIN Students s ON mf.StudentID=s.StudentID LEFT JOIN Enrollments e ON s.StudentID=e.StudentID WHERE COALESCE(s.IsArchived,0)=0 AND COALESCE(mf.AmountPaid,0) <= 0 AND COALESCE(mf.Amount,0) > 0" + mscope, msp, one=True)['c']
                fees_total = (fees_total or 0) + (mfees_total or 0)
                fees_paid = (fees_paid or 0) + (mfees_paid or 0)
                fees_unpaid_count = max(fees_unpaid_count or 0, mfees_unpaid_count or 0)
            else:
                total = query_db("SELECT COUNT(*) as c FROM Students WHERE IsArchived=0", one=True)['c']
                active = query_db("SELECT COUNT(*) as c FROM Students WHERE StatusID=1 AND IsArchived=0", one=True)['c']
                orphans = query_db("SELECT COUNT(*) as c FROM Orphans o JOIN Students s ON o.StudentID=s.StudentID WHERE s.IsArchived=0", one=True)['c']
                extra_total = query_db("SELECT COALESCE(SUM(f.Amount),0) as s FROM Fees f JOIN Students s ON f.StudentID=s.StudentID WHERE COALESCE(s.IsArchived,0)=0", one=True)['s']
                extra_paid = query_db("SELECT COALESCE(SUM(f.AmountPaid),0) as s FROM Fees f JOIN Students s ON f.StudentID=s.StudentID WHERE COALESCE(s.IsArchived,0)=0", one=True)['s']
                extra_unpaid_count = query_db("SELECT COUNT(*) as c FROM Fees f JOIN Students s ON f.StudentID=s.StudentID WHERE COALESCE(s.IsArchived,0)=0 AND COALESCE(f.AmountPaid,0) <= 0 AND COALESCE(f.Amount,0) > 0", one=True)['c']
                monthly_total = query_db("SELECT COALESCE(SUM(mf.Amount),0) as s FROM MonthlyFees mf JOIN Students s ON mf.StudentID=s.StudentID WHERE COALESCE(s.IsArchived,0)=0", one=True)['s']
                monthly_paid = query_db("SELECT COALESCE(SUM(mf.AmountPaid),0) as s FROM MonthlyFees mf JOIN Students s ON mf.StudentID=s.StudentID WHERE COALESCE(s.IsArchived,0)=0", one=True)['s']
                monthly_unpaid_count = query_db("""
                    SELECT COUNT(DISTINCT mf.StudentID) as c FROM MonthlyFees mf
                    JOIN Students s ON mf.StudentID=s.StudentID
                    WHERE COALESCE(s.IsArchived,0)=0 AND COALESCE(mf.AmountPaid,0) <= 0 AND COALESCE(mf.Amount,0) > 0
                """, one=True)['c']
                fees_total = (extra_total or 0) + (monthly_total or 0)
                fees_paid = (extra_paid or 0) + (monthly_paid or 0)
                fees_unpaid_count = max(extra_unpaid_count or 0, monthly_unpaid_count or 0)
            _fin = finance_summary_for_user(user)  # STUDENT_FINANCE_LEDGER_PATCH_V1
            fees_total = _fin['fees_total']
            fees_paid = _fin['fees_paid']
            fees_unpaid_count = _fin['fees_unpaid_count']
            self.send_json({
                "total": total, "active": active,
                "orphans": orphans,
                "fees_total": fees_total,
                "fees_paid": fees_paid,
                "fees_remaining": _fin['fees_remaining'],
                "fees_unpaid_count": fees_unpaid_count
            })

        elif path == '/api/reports/by_grade':
            rows = query_db("""
                SELECT gr.GradeName, COUNT(DISTINCT s.StudentID) as Count,
                       gr.GradeID
                FROM Grades gr
                LEFT JOIN (
                    SELECT StudentID, GradeID FROM Enrollments
                    UNION ALL
                    SELECT StudentID, GradeID FROM StudentClassMemberships
                ) e ON gr.GradeID=e.GradeID
                LEFT JOIN Students s ON s.StudentID=e.StudentID AND COALESCE(s.IsArchived,0)=0
                GROUP BY gr.GradeID  -- ARCHIVED_STUDENTS_FEES_PATCH_V1
                ORDER BY gr.GradeID
            """)
            self.send_json(rows)

        elif path == '/api/reports/by_gender':
            rows = query_db("""
                SELECT g.GenderName, COUNT(s.StudentID) as Count
                FROM Gender g
                LEFT JOIN Students s ON g.GenderID=s.GenderID AND COALESCE(s.IsArchived,0)=0  -- ARCHIVED_STUDENTS_FEES_PATCH_V1
                GROUP BY g.GenderID
            """)
            self.send_json(rows)

        elif path == '/api/notifications':
            self.send_json(build_notifications(user))

        elif path == '/api/class-memberships':
            if user.get('Role') != 'admin':
                self.send_json({"error": "إدارة العضويات الدراسية للمدير فقط"}, 403); return
            student_id = qp('student', '')
            grade_id = qp('grade', '')
            search = qp('search', '')
            sql = """
                WITH memberships AS (
                    SELECT NULL AS MembershipID, StudentID, GradeID, Section, AcademicYear,
                           'primary' AS MembershipKind, NULL AS Notes
                    FROM Enrollments
                    UNION ALL
                    SELECT MembershipID, StudentID, GradeID, Section, AcademicYear,
                           'additional' AS MembershipKind, Notes
                    FROM StudentClassMemberships
                )
                SELECT m.*, s.StudentNo, s.FullName, gr.GradeName
                FROM memberships m
                JOIN Students s ON s.StudentID=m.StudentID
                JOIN Grades gr ON gr.GradeID=m.GradeID
                WHERE COALESCE(s.IsArchived,0)=0
            """
            params = []
            if student_id:
                sql += " AND m.StudentID=?"; params.append(student_id)
            if grade_id:
                sql += " AND m.GradeID=?"; params.append(grade_id)
            if search:
                like = f"%{search}%"
                sql += " AND (s.FullName LIKE ? OR s.StudentNo LIKE ?)"; params += [like, like]
            sql += " ORDER BY s.FullName, m.MembershipKind DESC, gr.GradeID, m.Section"
            self.send_json(query_db(sql, params))

        elif path == '/api/cash-debts':
            if user.get('Role') != 'admin':
                self.send_json({"error": "سجل الديون النقدية للمدير فقط"}, 403); return
            search = qp('search', '')
            status = qp('status', '')
            sql = """
                SELECT d.DebtID, d.StudentID, d.Description, d.PrincipalAmount,
                       d.DebtDate, d.DueDate, d.Notes, d.CreatedAt,
                       s.StudentNo, s.FullName, s.Mobile1,
                       COALESCE(SUM(p.Amount),0) AS AmountPaid,
                       MAX(0, d.PrincipalAmount-COALESCE(SUM(p.Amount),0)) AS Remaining,
                       CASE
                         WHEN COALESCE(SUM(p.Amount),0) >= d.PrincipalAmount THEN 'paid'
                         WHEN COALESCE(SUM(p.Amount),0) > 0 THEN 'partial'
                         ELSE 'unpaid'
                       END AS PaymentStatus
                FROM CashDebts d
                JOIN Students s ON s.StudentID=d.StudentID
                LEFT JOIN CashDebtPayments p ON p.DebtID=d.DebtID
                WHERE 1=1
            """
            params = []
            if search:
                like = f"%{search}%"
                sql += " AND (s.FullName LIKE ? OR s.StudentNo LIKE ? OR d.Description LIKE ?)"
                params += [like, like, like]
            sql += " GROUP BY d.DebtID"
            if status == 'paid':
                sql += " HAVING COALESCE(SUM(p.Amount),0) >= d.PrincipalAmount"
            elif status == 'partial':
                sql += " HAVING COALESCE(SUM(p.Amount),0) > 0 AND COALESCE(SUM(p.Amount),0) < d.PrincipalAmount"
            elif status == 'unpaid':
                sql += " HAVING COALESCE(SUM(p.Amount),0) <= 0"
            sql += " ORDER BY d.DebtDate DESC, d.DebtID DESC"
            self.send_json(query_db(sql, params))

        elif path.startswith('/api/cash-debts/') and path.endswith('/payments'):
            if user.get('Role') != 'admin':
                self.send_json({"error": "سجل الديون النقدية للمدير فقط"}, 403); return
            debt_id = path.split('/')[3]
            self.send_json(query_db("""
                SELECT * FROM CashDebtPayments WHERE DebtID=?
                ORDER BY PaymentDate DESC, PaymentID DESC
            """, (debt_id,)))

        elif path == '/api/communications/recipients':
            if user.get('Role') != 'admin':
                self.send_json({"error": "قائمة الرسائل اليدوية للمدير فقط"}, 403); return
            search = qp('search', '')
            grade_id = qp('grade', '')
            payment_status = qp('payment_status', '')
            monthly_due = """COALESCE((SELECT MAX(COALESCE(mf.Amount,0)-COALESCE(mf.AmountPaid,0),0)
                FROM MonthlyFees mf WHERE mf.StudentID=s.StudentID
                ORDER BY mf.Year DESC,mf.Month DESC,mf.MonthlyFeeID DESC LIMIT 1),0)"""
            extra_due = """COALESCE((SELECT SUM(MAX(COALESCE(f.Amount,0)-COALESCE(f.AmountPaid,0),0))
                FROM Fees f WHERE f.StudentID=s.StudentID),0)"""
            credit = """COALESCE((SELECT CreditBalance FROM StudentFinanceAccounts a
                WHERE a.StudentID=s.StudentID),0)"""
            progress = """(COALESCE((SELECT AmountPaid FROM MonthlyFees mp WHERE mp.StudentID=s.StudentID
                ORDER BY mp.Year DESC,mp.Month DESC,mp.MonthlyFeeID DESC LIMIT 1),0)
                + COALESCE((SELECT SUM(CASE WHEN COALESCE(f2.Amount,0)>COALESCE(f2.AmountPaid,0)
                    THEN MAX(COALESCE(f2.AmountPaid,0),0) ELSE 0 END) FROM Fees f2 WHERE f2.StudentID=s.StudentID),0)
                + %s)""" % credit
            net_due = f"(({monthly_due})+({extra_due})-({credit}))"
            sql = f"""
                SELECT DISTINCT s.StudentID, s.StudentNo, s.FullName,
                       COALESCE(NULLIF(TRIM(s.Mobile1),''), NULLIF(TRIM(s.Mobile2),''),
                         (SELECT NULLIF(TRIM(p.Mobile),'') FROM ParentStudents ps
                          JOIN Parents p ON p.ParentID=ps.ParentID
                          WHERE ps.StudentID=s.StudentID AND COALESCE(TRIM(p.Mobile),'')<>''
                          ORDER BY p.ParentID LIMIT 1)) AS ContactMobile,
                       gr.GradeName, e.Section,
                       MAX(0,{net_due}) AS AmountDue,
                       CASE WHEN {net_due}<=0.009 THEN 'paid'
                            WHEN {progress}>0.009 THEN 'partial' ELSE 'unpaid' END AS PaymentStatus
                FROM Students s
                LEFT JOIN Enrollments e ON e.StudentID=s.StudentID
                LEFT JOIN Grades gr ON gr.GradeID=e.GradeID
                WHERE COALESCE(s.IsArchived,0)=0
            """
            params = []
            if search:
                like = f"%{search}%"
                sql += " AND (s.FullName LIKE ? OR s.StudentNo LIKE ? OR s.Mobile1 LIKE ? OR s.Mobile2 LIKE ?)"
                params += [like, like, like, like]
            if grade_id:
                sql += " AND (e.GradeID=? OR EXISTS (SELECT 1 FROM StudentClassMemberships scm WHERE scm.StudentID=s.StudentID AND scm.GradeID=?))"
                params += [grade_id, grade_id]
            if payment_status == 'paid':
                sql += f" AND {net_due}<=0.009"
            elif payment_status == 'partial':
                sql += f" AND {net_due}>0.009 AND {progress}>0.009"
            elif payment_status == 'unpaid':
                sql += f" AND {net_due}>0.009 AND {progress}<=0.009"
            sql += " AND COALESCE(NULLIF(TRIM(s.Mobile1),''), NULLIF(TRIM(s.Mobile2),''), (SELECT NULLIF(TRIM(p.Mobile),'') FROM ParentStudents ps JOIN Parents p ON p.ParentID=ps.ParentID WHERE ps.StudentID=s.StudentID AND COALESCE(TRIM(p.Mobile),'')<>'' LIMIT 1)) IS NOT NULL ORDER BY s.FullName"
            self.send_json(query_db(sql, params))

        elif path == '/api/communication-campaigns':
            if user.get('Role') != 'admin':
                self.send_json({"error": "قائمة الرسائل اليدوية للمدير فقط"}, 403); return
            self.send_json(query_db("""
                SELECT c.CampaignID, c.Title, c.MessageBody, c.Status, c.RecipientCount,
                       c.CreatedAt, c.ConfirmedAt, u.FullName AS CreatedByName
                FROM CommunicationCampaigns c
                LEFT JOIN Users u ON u.UserID=c.CreatedBy
                ORDER BY c.CampaignID DESC LIMIT 100
            """))

        elif path == '/api/backup/list':
            if user.get('Role') != 'admin':
                self.send_json({"error":"النسخ الاحتياطي للمدير فقط"}, 403); return
            ensure_backup_dir()
            items = []
            for fname in os.listdir(BACKUP_DIR):
                if not fname.endswith('.db'):
                    continue
                fpath = os.path.join(BACKUP_DIR, fname)
                try:
                    stat = os.stat(fpath)
                    items.append({
                        "filename": fname,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    })
                except OSError:
                    continue
            items.sort(key=lambda x: x['modified'], reverse=True)
            self.send_json(items)

        elif path == '/api/backup/download':
            if user.get('Role') != 'admin':
                self.send_json({"error":"النسخ الاحتياطي للمدير فقط"}, 403); return
            fname = qp('file', '')
            # حماية من الخروج عن مجلد النسخ الاحتياطية (path traversal)
            safe_name = os.path.basename(fname or '')
            fpath = os.path.join(BACKUP_DIR, safe_name)
            if not safe_name or not os.path.isfile(fpath):
                self.send_json({"error": "الملف غير موجود"}, 404); return
            with open(fpath, 'rb') as f:
                data = f.read()
            self.send_binary(data, 'application/octet-stream', safe_name)

        elif path == '/api/backup/download-current':
            if user.get('Role') != 'admin':
                self.send_json({"error":"النسخ الاحتياطي للمدير فقط"}, 403); return
            ensure_backup_dir()
            tmp_name = safe_backup_filename('students_snapshot')
            tmp_path = os.path.join(BACKUP_DIR, '.' + tmp_name)
            try:
                create_backup_file(tmp_path)
                with open(tmp_path, 'rb') as f:
                    data = f.read()
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            self.send_binary(data, 'application/octet-stream', tmp_name.lstrip('.'))

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        data = self.get_body()
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/auth/login':
            username = (data.get('Username') or data.get('username') or '').strip()
            password = data.get('Password') or data.get('password') or ''
            u = query_db('SELECT * FROM Users WHERE Username=? AND IsActive=1', (username,), one=True)
            if not u or not verify_password(password, u.get('PasswordHash','')):
                self.send_json({"error":"اسم المستخدم أو كلمة المرور غير صحيحة"}, 401); return
            token = secrets.token_urlsafe(32)
            execute_db("DELETE FROM UserSessions WHERE ExpiresAt IS NOT NULL AND datetime(ExpiresAt) <= datetime('now','localtime')")
            execute_db("INSERT INTO UserSessions (Token, UserID, ExpiresAt) VALUES (?,?,datetime('now','localtime','+12 hours'))", (token, u['UserID']))
            execute_db("UPDATE Users SET LastLogin=datetime('now','localtime') WHERE UserID=?", (u['UserID'],))
            log_activity(u['UserID'], 'تسجيل الدخول إلى النظام', 'POST', '/api/auth/login', None, u.get('Username'), u.get('FullName'))
            self.send_json({"token":token,"user":{"UserID":u['UserID'],"Username":u['Username'],"FullName":u['FullName'],"Role":u['Role'],"CanEnterMarks":u.get('CanEnterMarks',1)}})
            return

        user = self.require_auth()
        if not user: return
        teacher_allowed_paths = ['/api/auth/logout', '/api/marks', '/api/attendance', '/api/attendance/bulk']
        teacher_allowed = path in teacher_allowed_paths or path.endswith('/graduation-project')
        if user.get('Role') != 'admin' and not teacher_allowed:
            self.send_json({"error":"حساب المعلم لا يملك صلاحية هذه العملية"}, 403); return

        if path == '/api/auth/logout':
            log_activity(user['UserID'], 'تسجيل الخروج من النظام', 'POST', '/api/auth/logout', None, user.get('Username'), user.get('FullName'))
            auth = self.headers.get('Authorization','')
            token = auth.split(' ',1)[1] if auth.startswith('Bearer ') else ''
            if token: execute_db('DELETE FROM UserSessions WHERE Token=?', (token,))
            self._activity_logged = True
            self.send_json({"message":"تم تسجيل الخروج"})

        elif path == '/api/backup/create':
            # (تسجيل الدخول كمدير مطلوب أصلاً، محقق في الأعلى)
            ensure_backup_dir()
            fname = safe_backup_filename()
            fpath = os.path.join(BACKUP_DIR, fname)
            try:
                create_backup_file(fpath)
            except Exception as e:
                self.send_json({"error": f"فشل إنشاء النسخة الاحتياطية: {e}"}, 500); return
            self.send_json({"filename": fname, "message": "تم إنشاء نسخة احتياطية بنجاح"})

        elif path == '/api/backup/restore':
            ensure_backup_dir()
            filename = data.get('filename')
            file_data_b64 = data.get('FileData')
            temp_upload_path = None
            try:
                if filename:
                    # استعادة من نسخة موجودة مسبقاً على السيرفر
                    safe_name = os.path.basename(filename)
                    source_path = os.path.join(BACKUP_DIR, safe_name)
                    if not os.path.isfile(source_path):
                        self.send_json({"error": "النسخة الاحتياطية غير موجودة"}, 404); return
                elif file_data_b64:
                    # استعادة من ملف تم رفعه من جهاز المستخدم
                    try:
                        raw = base64.b64decode(file_data_b64.split(',')[-1])
                    except Exception:
                        self.send_json({"error": "ملف النسخة الاحتياطية غير صالح"}, 400); return
                    temp_upload_path = os.path.join(BACKUP_DIR, f".uploaded_{secrets.token_hex(6)}.db")
                    with open(temp_upload_path, 'wb') as f:
                        f.write(raw)
                    if not is_valid_sqlite_file(temp_upload_path):
                        os.remove(temp_upload_path)
                        self.send_json({"error": "الملف المرفوع ليس ملف قاعدة بيانات صالحاً"}, 400); return
                    source_path = temp_upload_path
                else:
                    self.send_json({"error": "يرجى اختيار نسخة احتياطية أو رفع ملف"}, 400); return

                # نسخة أمان تلقائية من الوضع الحالي قبل الاستبدال، حتى يمكن التراجع دائماً
                safety_name = safe_backup_filename('students_before_restore')
                create_backup_file(os.path.join(BACKUP_DIR, safety_name))

                restore_backup_file(source_path)
                self.send_json({"message": "تمت استعادة النسخة الاحتياطية بنجاح", "safetyBackup": safety_name})
            except Exception as e:
                self.send_json({"error": f"فشلت عملية الاستعادة: {e}"}, 500)
            finally:
                if temp_upload_path and os.path.exists(temp_upload_path):
                    os.remove(temp_upload_path)

        elif path == '/api/users':
            if user.get('Role') != 'admin': self.send_json({"error":"للمدير فقط"},403); return
            username=(data.get('Username') or '').strip(); password=data.get('Password') or ''; fullname=(data.get('FullName') or '').strip(); role=data.get('Role') or 'teacher'
            if not username or not fullname:
                self.send_json({"error":"اسم المستخدم والاسم الكامل مطلوبان"},400); return
            if len(password) < 10:
                self.send_json({"error":"أدخل كلمة مرور خاصة لا تقل عن 10 أحرف عند إنشاء المستخدم"},400); return
            if role not in ('admin', 'teacher'):
                self.send_json({"error":"دور المستخدم غير صالح"},400); return
            try:
                uid=execute_db("INSERT INTO Users (Username,PasswordHash,FullName,Role,IsActive,CanEnterMarks) VALUES (?,?,?,?,?,?)", (username, hash_password(password), fullname, role, 1 if data.get('IsActive',1) else 0, 1 if data.get('CanEnterMarks',1) else 0))
            except sqlite3.IntegrityError:
                self.send_json({"error":"اسم المستخدم موجود مسبقاً"},400); return
            teacher_serial = None
            if role=='teacher':
                teacher_serial = next_teacher_serial()
                execute_db("INSERT INTO TeacherProfiles (UserID,NationalID,Phone,SerialNo,Subject,Notes) VALUES (?,?,?,?,?,?)", (uid,data.get('NationalID'),data.get('Phone'),teacher_serial,data.get('Subject'),data.get('Notes')))
                save_teacher_subjects(uid, data.get('Subjects') or ([data.get('Subject')] if data.get('Subject') else []))
                for item in data.get('Grades',[]):
                    execute_db("INSERT OR IGNORE INTO TeacherGrades (UserID,GradeID,Section) VALUES (?,?,?)", (uid,item.get('GradeID'),item.get('Section') or None))
            response = {"UserID":uid,"message":"تم إضافة المستخدم"}
            if teacher_serial:
                response["SerialNo"] = teacher_serial
                response["message"] = f"تم إضافة المعلم بالرقم التسلسلي {teacher_serial}"
            self.send_json(response)

        elif path.startswith('/api/users/') and path.endswith('/change-password'):
            if user.get('Role') != 'admin': self.send_json({"error":"للمدير فقط"},403); return
            uid=path.split('/')[3]; pwd=data.get('Password') or ''
            if len(pwd)<10: self.send_json({"error":"كلمة المرور يجب ألا تقل عن 10 أحرف"},400); return
            execute_db("UPDATE Users SET PasswordHash=? WHERE UserID=?", (hash_password(pwd), uid))
            execute_db("DELETE FROM UserSessions WHERE UserID=?", (uid,))
            self.send_json({"message":"تم تغيير كلمة المرور وإلغاء جلسات المستخدم القديمة"})

        elif path == '/api/students':
            if user.get('Role') != 'admin': self.send_json({"error":"إضافة الطلاب للمدير فقط"},403); return
            validation_errors = validate_student_data(data)
            if validation_errors:
                self.send_json({"error": " / ".join(validation_errors)}, 400)
                return
            if data.get('NationalID'):
                existing = query_db("SELECT StudentID FROM Students WHERE NationalID=?", (data['NationalID'],))
                if existing:
                    self.send_json({"error": "رقم الهوية مكرر"}, 400)
                    return
            if data.get('StudentNo'):
                existing = query_db("SELECT StudentID FROM Students WHERE StudentNo=?", (data['StudentNo'],))
                if existing:
                    self.send_json({"error": "رقم الطالب مكرر"}, 400)
                    return
            sid = execute_db("""
                INSERT INTO Students (StudentNo, NationalID, FullName, GenderID,
                    BirthDate, Mobile1, Mobile2, Address, StatusID, GovID, Notes, MonthlyFee, Photo)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get('StudentNo'), data.get('NationalID'), data.get('FullName'),
                data.get('GenderID'), data.get('BirthDate'), data.get('Mobile1'),
                data.get('Mobile2'), data.get('Address'), data.get('StatusID', 1),
                data.get('GovID'), data.get('Notes'), (60 if data.get('MonthlyFee') in (None, '') else data.get('MonthlyFee')), data.get('Photo')
            ))
            # Add enrollment if grade provided
            if data.get('GradeID'):
                execute_db("""
                    INSERT INTO Enrollments (StudentID, GradeID, Section, AcademicYear)
                    VALUES (?,?,?,?)
                """, (sid, data.get('GradeID'), data.get('Section', 'أ'), data.get('AcademicYear', '2026-2027')))
            # إضافة ولي أمر تلقائياً حسب الاسم الثاني والثالث والرابع من اسم الطالب
            auto_parent_name = build_parent_name_from_student(data.get('FullName'))
            if auto_parent_name:
                # إن كان لأحد إخوته المسجّلين سابقاً نفس ولي الأمر يُعاد استخدام سجله نفسه
                auto_pid = find_or_create_parent(auto_parent_name, 'أب', data.get('Mobile1'))
                set_student_parent(sid, auto_pid)
            ensure_current_monthly_fee(sid)
            self.send_json({"StudentID": sid, "message": "تم إضافة الطالب بنجاح مع رسم شهري تلقائي"})

        elif path == '/api/grades':
            name = (data.get('GradeName') or '').strip()
            if not name:
                self.send_json({"error": "يرجى إدخال اسم الصف"}, 400)
                return
            gid = data.get('GradeID')
            if gid in (None, ''):
                gid = next_grade_id()
            else:
                try:
                    gid = int(gid)
                except (TypeError, ValueError):
                    self.send_json({"error": "رقم الصف غير صحيح"}, 400)
                    return
                existing = query_db("SELECT GradeID FROM Grades WHERE GradeID=?", (gid,))
                if existing:
                    self.send_json({"error": "رقم الصف هذا مستخدم بالفعل"}, 400)
                    return
            execute_db("INSERT INTO Grades (GradeID, GradeName) VALUES (?,?)", (gid, name))
            # إضافة المواد الأساسية تلقائياً لكل صف جديد (لغة عربية، لغة إنجليزية، رياضيات، اجتماعيات)
            for core_name in ["لغة عربية", "لغة إنجليزية", "رياضيات", "اجتماعيات"]:
                srow = query_db("SELECT SubjectID FROM Subjects WHERE SubjectName=?", (core_name,), one=True)
                if srow:
                    execute_db("INSERT OR IGNORE INTO GradeSubjects (GradeID, SubjectID) VALUES (?,?)", (gid, srow['SubjectID']))
            self.send_json({"GradeID": gid, "message": "تم إضافة الصف بنجاح"})

        elif path == '/api/fees/reset-all':
            # تصفير المبلغ المدفوع لكل الرسوم لكل الطلاب، مع الإبقاء على المبلغ المطلوب (Amount) كما هو
            execute_db("UPDATE Fees SET AmountPaid=0, PaidDate=NULL")
            self.send_json({"message": "تم تصفير الحساب المالي لجميع الطلاب بنجاح"})

        elif path.startswith('/api/students/') and path.endswith('/fees/reset'):
            sid = path.split('/')[3]
            execute_db("UPDATE Fees SET AmountPaid=0, PaidDate=NULL WHERE StudentID=?", (sid,))
            self.send_json({"message": "تم تصفير الحساب المالي لهذا الطالب بنجاح"})

        elif path == '/api/parents':
            if user.get('Role') != 'admin': self.send_json({"error":"إدارة أولياء الأمور للمدير فقط"},403); return
            student_id = data.get('StudentID')
            if not student_id:
                self.send_json({"error": "يجب تحديد الطالب"}, 400); return
            existing_parent_id = data.get('ParentID')  # إن أُرسل: ربط ولي أمر موجود مسبقاً (حالة الإخوة) بدل إنشاء سجل جديد
            if existing_parent_id:
                pid = existing_parent_id
            else:
                if not (data.get('ParentName') or '').strip():
                    self.send_json({"error": "يرجى إدخال اسم ولي الأمر"}, 400); return
                pid = execute_db("""
                    INSERT INTO Parents (ParentName, Relation, Mobile, Job, NationalID)
                    VALUES (?,?,?,?,?)
                """, (
                    data.get('ParentName'), data.get('Relation'),
                    data.get('Mobile'), data.get('Job'), data.get('NationalID')
                ))
            # كل طالب له ولي أمر واحد فقط: يستبدل هذا أي ربط سابق لهذا الطالب تلقائياً
            set_student_parent(student_id, pid)
            self.send_json({"ParentID": pid, "message": "تم ربط ولي الأمر بالطالب بنجاح"})

        elif path == '/api/orphans':
            execute_db("""
                INSERT OR REPLACE INTO Orphans (StudentID, OrphanType, OrphanDate, SupportedBy, Notes)
                VALUES (?,?,?,?,?)
            """, (
                data['StudentID'], data.get('OrphanType'), data.get('OrphanDate'),
                data.get('SupportedBy'), data.get('Notes')
            ))
            self.send_json({"message": "تم حفظ بيانات اليتيم"})

        elif path == '/api/health-status':
            execute_db("""
                INSERT INTO HealthStatus (StudentID, HealthType, Notes, UpdatedDate)
                VALUES (?,?,?, date('now'))
                ON CONFLICT(StudentID) DO UPDATE SET
                    HealthType=excluded.HealthType,
                    Notes=excluded.Notes,
                    UpdatedDate=date('now')
            """, (
                data['StudentID'], data.get('HealthType'), data.get('Notes')
            ))
            self.send_json({"message": "تم حفظ الحالة الصحية"})

        elif path == '/api/financial-status':
            execute_db("""
                INSERT INTO FinancialStatus (StudentID, FinCategoryID, Notes, UpdatedDate)
                VALUES (?,?,?, date('now'))
                ON CONFLICT(StudentID) DO UPDATE SET
                    FinCategoryID=excluded.FinCategoryID,
                    Notes=excluded.Notes,
                    UpdatedDate=date('now')
            """, (
                data['StudentID'], data.get('FinCategoryID') or None, data.get('Notes')
            ))
            self.send_json({"message": "تم حفظ الحالة المادية"})

        elif path == '/api/fees':
            fid = execute_db("""
                INSERT INTO Fees (StudentID, FeeType, Amount, AmountPaid, DueDate, AcademicYear, Notes, Photo)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                data['StudentID'], data.get('FeeType', 'رسوم دراسية'),
                data.get('Amount', 0), data.get('AmountPaid', 0),
                data.get('DueDate'), data.get('AcademicYear', '2026-2027'),
                data.get('Notes'), data.get('Photo')
            ))
            self.send_json({"FeeID": fid, "message": "تم إضافة الرسوم"})

        elif path == '/api/fees/pay':
            try:
                result = finance_process_payment(
                    'extra', data.get('FeeID'), data.get('Amount',0), data.get('PaidDate'),
                    data.get('ReceivedBy','أمين الصندوق'), data.get('Notes',''), data.get('Photo'),
                    data.get('PaymentMethod','كاش'), bool(data.get('UseCredit', True))
                )
            except ValueError as e:
                self.send_json({'error':str(e)},404); return
            self.send_json({
                'ReceiptID':result.get('ReceiptID'), 'message':result.get('Message'),
                'CreditBalance':result.get('CreditBalance'), 'CreditUsed':result.get('CreditUsed'),
                'ExcessToCredit':result.get('ExcessToCredit')
            })
        elif path == '/api/monthly-fees/pay':
            try:
                result = finance_process_payment(
                    'monthly', data.get('MonthlyFeeID'), data.get('Amount',0), data.get('PaidDate'),
                    data.get('ReceivedBy','أمين الصندوق'), data.get('Notes',''), data.get('Photo'),
                    data.get('PaymentMethod','كاش'), bool(data.get('UseCredit', True))
                )
            except ValueError as e:
                self.send_json({'error':str(e)},404); return
            self.send_json({
                'ReceiptID':result.get('ReceiptID'), 'message':result.get('Message'),
                'CreditBalance':result.get('CreditBalance'), 'CreditUsed':result.get('CreditUsed'),
                'ExcessToCredit':result.get('ExcessToCredit')
            })
        elif path == '/api/enrollments':
            eid = execute_db("""
                INSERT OR REPLACE INTO Enrollments (StudentID, GradeID, Section, AcademicYear)
                VALUES (?,?,?,?)
            """, (
                data['StudentID'], data.get('GradeID'),
                data.get('Section', 'أ'), data.get('AcademicYear', '2026-2027')
            ))
            self.send_json({"EnrollID": eid, "message": "تم تسجيل الطالب في الصف"})

        elif path == '/api/class-memberships':
            student_id = data.get('StudentID')
            grade_id = data.get('GradeID')
            section = (data.get('Section') or 'أ').strip()
            academic_year = (data.get('AcademicYear') or '2026-2027').strip()
            if not student_id or grade_id in (None, ''):
                self.send_json({"error": "اختر الطالب والصف"}, 400); return
            if not query_db("SELECT StudentID FROM Students WHERE StudentID=? AND COALESCE(IsArchived,0)=0", (student_id,), one=True):
                self.send_json({"error": "الطالب غير موجود أو مؤرشف"}, 404); return
            if not query_db("SELECT GradeID FROM Grades WHERE GradeID=?", (grade_id,), one=True):
                self.send_json({"error": "الصف غير موجود"}, 404); return
            primary = query_db("""
                SELECT 1 AS Found FROM Enrollments
                WHERE StudentID=? AND GradeID=? AND COALESCE(Section,'')=? AND COALESCE(AcademicYear,'')=?
            """, (student_id, grade_id, section, academic_year), one=True)
            if primary:
                self.send_json({"error": "هذه العضوية هي الصف الأساسي للطالب بالفعل"}, 400); return
            try:
                membership_id = execute_db("""
                    INSERT INTO StudentClassMemberships
                    (StudentID, GradeID, Section, AcademicYear, Notes)
                    VALUES (?,?,?,?,?)
                """, (student_id, grade_id, section, academic_year, data.get('Notes')))
            except sqlite3.IntegrityError:
                self.send_json({"error": "الطالب مسجل مسبقاً في هذه المجموعة"}, 400); return
            self.send_json({"MembershipID": membership_id, "message": "تمت إضافة العضوية الدراسية الإضافية"})

        elif path == '/api/cash-debts':
            student_id = data.get('StudentID')
            description = (data.get('Description') or '').strip()
            try:
                amount = float(data.get('PrincipalAmount') or 0)
            except (TypeError, ValueError):
                amount = 0
            if not student_id or not description or amount <= 0:
                self.send_json({"error": "الطالب ووصف الدين ومبلغ موجب مطلوبة"}, 400); return
            debt_id = execute_db("""
                INSERT INTO CashDebts
                (StudentID, Description, PrincipalAmount, DebtDate, DueDate, Notes, CreatedBy)
                VALUES (?,?,?,?,?,?,?)
            """, (student_id, description, amount, data.get('DebtDate') or datetime.now().strftime('%Y-%m-%d'),
                  data.get('DueDate') or None, data.get('Notes'), user['UserID']))
            self.send_json({"DebtID": debt_id, "message": "تم تسجيل الدين النقدي في ملف الطالب"})

        elif path.startswith('/api/cash-debts/') and path.endswith('/pay'):
            debt_id = path.split('/')[3]
            debt = query_db("""
                SELECT d.PrincipalAmount, COALESCE(SUM(p.Amount),0) AS AmountPaid
                FROM CashDebts d LEFT JOIN CashDebtPayments p ON p.DebtID=d.DebtID
                WHERE d.DebtID=? GROUP BY d.DebtID
            """, (debt_id,), one=True)
            if not debt:
                self.send_json({"error": "الدين غير موجود"}, 404); return
            try:
                amount = float(data.get('Amount') or 0)
            except (TypeError, ValueError):
                amount = 0
            remaining = max(0.0, float(debt['PrincipalAmount'] or 0)-float(debt['AmountPaid'] or 0))
            if amount <= 0 or amount > remaining + 0.009:
                self.send_json({"error": f"مبلغ الدفعة يجب أن يكون أكبر من صفر ولا يتجاوز {remaining:.2f} ₪"}, 400); return
            payment_id = execute_db("""
                INSERT INTO CashDebtPayments (DebtID, Amount, PaymentDate, ReceivedBy, Notes)
                VALUES (?,?,?,?,?)
            """, (debt_id, amount, data.get('PaymentDate') or datetime.now().strftime('%Y-%m-%d'),
                  data.get('ReceivedBy') or user.get('FullName'), data.get('Notes')))
            self.send_json({"PaymentID": payment_id, "message": "تم تسجيل دفعة الدين النقدي"})

        elif path == '/api/communication-campaigns':
            title = (data.get('Title') or '').strip()
            message = (data.get('MessageBody') or '').strip()
            confirmed = bool(data.get('Confirmed'))
            raw_ids = data.get('StudentIDs') or []
            try:
                student_ids = sorted({int(x) for x in raw_ids})
            except (TypeError, ValueError):
                student_ids = []
            if not confirmed:
                self.send_json({"error": "يجب تأكيد القائمة يدوياً قبل اعتمادها"}, 400); return
            if not title or not message or not student_ids:
                self.send_json({"error": "العنوان والنص ومستلم واحد على الأقل مطلوبة"}, 400); return
            placeholders = ','.join('?' for _ in student_ids)
            recipients = query_db(f"""
                SELECT s.StudentID,
                       COALESCE(NULLIF(TRIM(s.Mobile1),''), NULLIF(TRIM(s.Mobile2),''),
                         (SELECT NULLIF(TRIM(p.Mobile),'') FROM ParentStudents ps
                          JOIN Parents p ON p.ParentID=ps.ParentID
                          WHERE ps.StudentID=s.StudentID AND COALESCE(TRIM(p.Mobile),'')<>''
                          ORDER BY p.ParentID LIMIT 1)) AS Mobile
                FROM Students s
                WHERE COALESCE(s.IsArchived,0)=0 AND s.StudentID IN ({placeholders})
            """, student_ids)
            recipients = [r for r in recipients if r.get('Mobile')]
            if not recipients:
                self.send_json({"error": "لا يوجد رقم جوال صالح ضمن الطلاب المحددين"}, 400); return
            conn = get_connection()
            try:
                cur = conn.execute("""
                    INSERT INTO CommunicationCampaigns
                    (Title, MessageBody, Status, RecipientCount, CreatedBy, ConfirmedAt)
                    VALUES (?,?,'confirmed_manual',?,?,datetime('now','localtime'))
                """, (title, message, len(recipients), user['UserID']))
                campaign_id = cur.lastrowid
                conn.executemany("""
                    INSERT INTO CommunicationRecipients (CampaignID, StudentID, Mobile)
                    VALUES (?,?,?)
                """, [(campaign_id, r['StudentID'], r['Mobile']) for r in recipients])
                conn.commit()
            finally:
                conn.close()
            self.send_json({
                "CampaignID": campaign_id,
                "RecipientCount": len(recipients),
                "message": "تم اعتماد القائمة للمراجعة والإرسال اليدوي فقط؛ لم تُرسل أي رسالة تلقائياً"
            })

        elif path.startswith('/api/receipts/') and path.endswith('/print-confirm'):
            receipt_id = path.split('/')[3]
            receipt = query_db("SELECT ReceiptID FROM FeeReceipts WHERE ReceiptID=?", (receipt_id,), one=True)
            if not receipt:
                self.send_json({"error": "سند القبض غير موجود"}, 404); return
            execute_db("""
                UPDATE FeeReceipts SET IsPrinted=1,
                    PrintedAt=COALESCE(PrintedAt, datetime('now','localtime'))
                WHERE ReceiptID=?
            """, (receipt_id,))
            self.send_json({"message": "تم اعتماد سند القبض بعد طلب الطباعة"})

        elif path == '/api/grade-subjects':
            if user.get('Role') != 'admin': self.send_json({"error":"للمدير فقط"},403); return
            gid = data.get('GradeID')
            subs = data.get('Subjects') or []
            if not gid: self.send_json({"error":"اختر الصف أولاً"},400); return
            execute_db("DELETE FROM GradeSubjects WHERE GradeID=?", (gid,))
            for s in subs:
                sid = s.get('SubjectID') if isinstance(s, dict) else s
                if sid:
                    try:
                        execute_db("INSERT OR IGNORE INTO GradeSubjects (GradeID, SubjectID) VALUES (?,?)", (gid, sid))
                    except sqlite3.IntegrityError:
                        pass
            self.send_json({"message":"تم حفظ مواد الصف"})

        elif path == '/api/subjects':
            if user.get('Role') != 'admin': self.send_json({"error":"للمدير فقط"},403); return
            name=(data.get('SubjectName') or '').strip()
            if not name: self.send_json({"error":"أدخل اسم المادة"},400); return
            try:
                sid=execute_db("INSERT INTO Subjects (SubjectName,Notes) VALUES (?,?)", (name,data.get('Notes')))
            except sqlite3.IntegrityError:
                self.send_json({"error":"هذه المادة موجودة مسبقاً"},400); return
            self.send_json({"SubjectID":sid,"message":"تمت إضافة المادة"})


        elif path == '/api/students/import':
            if not OPENPYXL_AVAILABLE:
                self.send_json({"error": "مكتبة openpyxl غير مثبتة على هذا الجهاز. شغّل: pip install openpyxl"}, 500)
                return
            file_b64 = data.get('fileBase64')
            if not file_b64:
                self.send_json({"error": "لم يتم إرفاق أي ملف"}, 400)
                return
            try:
                if ',' in file_b64 and file_b64.strip().startswith('data:'):
                    file_b64 = file_b64.split(',', 1)[1]
                file_bytes = base64.b64decode(file_b64)
                result = import_students_from_excel(file_bytes)
            except Exception as e:
                self.send_json({"error": f"تعذرت قراءة الملف. تأكد أنه بصيغة Excel (.xlsx) صحيحة. ({e})"}, 400)
                return
            self.send_json(result)

        elif path.startswith('/api/students/') and path.endswith('/graduation-project'):
            sid = path.split('/')[3]
            if not student_allowed_for_user(user, sid):
                self.send_json({"error":"لا تملك صلاحية تعديل مشروع هذا الطالب"}, 403); return
            title = (data.get('ProjectTitle') or '').strip()
            description = data.get('Description') or ''
            supervisor = data.get('Supervisor') or ''
            status_value = data.get('ProjectStatus') or 'لم يبدأ'
            grade_value = data.get('ProjectGrade')
            if grade_value in ('', None):
                grade_value = None
            else:
                try:
                    grade_value = float(grade_value)
                except (TypeError, ValueError):
                    self.send_json({"error":"علامة المشروع غير صحيحة"}, 400); return
            submission_date = data.get('SubmissionDate') or None
            notes = data.get('Notes') or ''
            existing = query_db("SELECT ProjectID FROM GraduationProjects WHERE StudentID=?", (sid,), one=True)
            if existing:
                execute_db("""
                    UPDATE GraduationProjects SET ProjectTitle=?, Description=?, Supervisor=?, ProjectStatus=?,
                        ProjectGrade=?, SubmissionDate=?, Notes=?, UpdatedBy=?, UpdatedAt=datetime('now','localtime')
                    WHERE StudentID=?
                """, (title, description, supervisor, status_value, grade_value, submission_date, notes, user['UserID'], sid))
                pid = existing['ProjectID']
            else:
                pid = execute_db("""
                    INSERT INTO GraduationProjects
                    (StudentID,ProjectTitle,Description,Supervisor,ProjectStatus,ProjectGrade,SubmissionDate,Notes,UpdatedBy)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (sid, title, description, supervisor, status_value, grade_value, submission_date, notes, user['UserID']))
            self.send_json({"ProjectID": pid, "message": "تم حفظ مشروع التخرج بنجاح"})

        elif path == '/api/marks':
            if user.get('Role') == 'teacher' and not user_can_enter_marks(user):
                self.send_json({"error":"تم إغلاق إدخال العلامات لحسابك من مدير النظام"}, 403); return
            sid = data.get('StudentID')
            if not sid:
                self.send_json({"error":"اختر الطالب أولاً"}, 400); return
            if not student_allowed_for_user(user, sid):
                self.send_json({"error":"لا تملك صلاحية إدخال علامات لهذا الطالب"}, 403); return
            if user.get('Role') == 'teacher':
                subject = (data.get('Subject') or '').strip()
                allowed_subjects = teacher_subject_names(user['UserID'])
                if not subject and allowed_subjects:
                    subject = allowed_subjects[0]
                if subject not in allowed_subjects:
                    self.send_json({"error":"لا تملك صلاحية إدخال علامات لهذه المادة"}, 403); return
                teacher_id = user['UserID']
            else:
                subject = (data.get('Subject') or '').strip() or 'المادة'
                teacher_id = data.get('TeacherUserID') or user['UserID']
            mark = data.get('Mark')
            max_mark = data.get('MaxMark') or 100
            exam = data.get('ExamName') or 'علامة'
            notes = data.get('Notes') or ''
            existing = query_db('SELECT MarkID FROM StudentMarks WHERE StudentID=? AND TeacherUserID=? AND Subject=? AND ExamName=?', (sid, teacher_id, subject, exam), one=True)
            if existing:
                execute_db("UPDATE StudentMarks SET Mark=?, MaxMark=?, Notes=?, UpdatedAt=datetime('now','localtime') WHERE MarkID=?", (mark, max_mark, notes, existing['MarkID']))
                mid = existing['MarkID']
            else:
                mid = execute_db('INSERT INTO StudentMarks (StudentID,TeacherUserID,Subject,ExamName,Mark,MaxMark,Notes) VALUES (?,?,?,?,?,?,?)', (sid, teacher_id, subject, exam, mark, max_mark, notes))
            self.send_json({"MarkID":mid,"message":"تم حفظ العلامة بنجاح"})

        elif path == '/api/attendance':
            sid = data.get('StudentID')
            date_ = (data.get('AttendanceDate') or '').strip()
            if not sid or not date_:
                self.send_json({"error":"اختر الطالب والتاريخ أولاً"}, 400); return
            if not student_allowed_for_user(user, sid):
                self.send_json({"error":"لا تملك صلاحية تسجيل حضور هذا الطالب"}, 403); return
            status = data.get('Status') or 'حاضر'
            notes = data.get('Notes') or ''
            existing = query_db('SELECT AttendanceID FROM Attendance WHERE StudentID=? AND AttendanceDate=?', (sid, date_), one=True)
            if existing:
                execute_db("UPDATE Attendance SET Status=?, Notes=?, RecordedBy=?, UpdatedAt=datetime('now','localtime') WHERE AttendanceID=?", (status, notes, user['UserID'], existing['AttendanceID']))
                aid = existing['AttendanceID']
            else:
                aid = execute_db('INSERT INTO Attendance (StudentID,AttendanceDate,Status,Notes,RecordedBy) VALUES (?,?,?,?,?)', (sid, date_, status, notes, user['UserID']))
            self.send_json({"AttendanceID":aid,"message":"تم حفظ الحضور بنجاح"})

        elif path == '/api/attendance/bulk':
            date_ = (data.get('AttendanceDate') or '').strip()
            records = data.get('Records') or []
            if not date_ or not records:
                self.send_json({"error":"لا توجد بيانات لحفظها"}, 400); return
            saved = 0
            for rec in records:
                sid = rec.get('StudentID')
                if not sid or not student_allowed_for_user(user, sid):
                    continue
                status = rec.get('Status') or 'حاضر'
                notes = rec.get('Notes') or ''
                existing = query_db('SELECT AttendanceID FROM Attendance WHERE StudentID=? AND AttendanceDate=?', (sid, date_), one=True)
                if existing:
                    execute_db("UPDATE Attendance SET Status=?, Notes=?, RecordedBy=?, UpdatedAt=datetime('now','localtime') WHERE AttendanceID=?", (status, notes, user['UserID'], existing['AttendanceID']))
                else:
                    execute_db('INSERT INTO Attendance (StudentID,AttendanceDate,Status,Notes,RecordedBy) VALUES (?,?,?,?,?)', (sid, date_, status, notes, user['UserID']))
                saved += 1
            self.send_json({"saved": saved, "message": f"تم حفظ حضور {saved} طالب/ة بنجاح"})

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        data = self.get_body()
        parsed = urlparse(self.path)
        path = parsed.path
        user = self.require_auth()
        if not user: return
        # تحديث صورة الطالب: للمدير فقط، وبمعالجة مبكرة حتى لا تُعامل كتعديل بيانات طالب عادي
        if path.startswith('/api/students/') and path.endswith('/photo'):
            if user.get('Role') != 'admin':
                self.send_json({"error":"تغيير صورة الطالب للمدير فقط"}, 403); return
            sid = path.split('/')[3]
            photo = data.get('Photo') or ''
            if photo and len(photo) > 2500000:
                self.send_json({"error":"حجم الصورة كبير جداً. اختر صورة أصغر."}, 400); return
            execute_db("UPDATE Students SET Photo=? WHERE StudentID=?", (photo, sid))
            self.send_json({"message":"تم حفظ صورة الطالب بنجاح"})
            return

        if user.get('Role') != 'admin':
            self.send_json({"error":"التعديل للمدير فقط"}, 403); return

        if path.startswith('/api/students/') and path.endswith('/archive'):
            sid = path.split('/')[3]
            reason = str(data.get('ArchiveReason') or '').strip()
            note = str(data.get('ArchiveNote') or '').strip()
            if not reason:
                self.send_json({"error": "يرجى اختيار سبب الأرشفة"}, 400); return
            execute_db(
                "UPDATE Students SET IsArchived=1, ArchivedDate=date('now'), ArchiveReason=?, ArchiveNote=? WHERE StudentID=?",
                (reason, note, sid)
            )
            self.send_json({"message": "تمت أرشفة الطالب مع حفظ السبب والملاحظة بنجاح"})
            return

        elif path.startswith('/api/students/') and path.endswith('/archive-note'):
            sid = path.split('/')[3]
            student = query_db("SELECT IsArchived FROM Students WHERE StudentID=?", (sid,), one=True)
            if not student:
                self.send_json({"error": "الطالب غير موجود"}, 404); return
            if not student.get('IsArchived'):
                self.send_json({"error": "يمكن تعديل ملاحظة الأرشفة للطالب المؤرشف فقط"}, 400); return
            reason = str(data.get('ArchiveReason') or '').strip()
            note = str(data.get('ArchiveNote') or '').strip()
            if not reason:
                self.send_json({"error": "يرجى اختيار سبب الأرشفة"}, 400); return
            execute_db(
                "UPDATE Students SET ArchiveReason=?, ArchiveNote=? WHERE StudentID=?",
                (reason, note, sid)
            )
            self.send_json({"message": "تم تعديل سبب وملاحظة الأرشفة بنجاح"})
            return

        elif path.startswith('/api/students/') and path.endswith('/restore'):
            sid = path.split('/')[3]
            execute_db(
                "UPDATE Students SET IsArchived=0, ArchivedDate=NULL WHERE StudentID=?",
                (sid,)
            )
            # بعد الاستعادة نضمن فاتورة الشهر الحالي فقط؛ لا نُنشئ رسوماً عن أشهر كان فيها الطالب مؤرشفاً.
            ensure_current_monthly_fee(sid)  # ARCHIVED_STUDENTS_FEES_PATCH_V1
            self.send_json({"message": "تمت استعادة الطالب من الأرشيف بنجاح"})
            return

        if path.startswith('/api/subjects/'):
            sid=path.split('/')[3]
            name=(data.get('SubjectName') or '').strip()
            if not name: self.send_json({"error":"أدخل اسم المادة"},400); return
            execute_db("UPDATE Subjects SET SubjectName=?, Notes=? WHERE SubjectID=?", (name,data.get('Notes'),sid))
            self.send_json({"message":"تم تعديل المادة"})

        elif path.startswith('/api/users/'):
            if user.get('Role') != 'admin': self.send_json({"error":"للمدير فقط"},403); return
            uid=path.split('/')[3]
            role=data.get('Role') or 'teacher'
            # نحفظ الرقم الحالي قبل إعادة بناء ملف المعلم، حتى يبقى ثابتاً عند التعديل.
            old_profile = query_db("SELECT SerialNo FROM TeacherProfiles WHERE UserID=?", (uid,), one=True)
            teacher_serial = (old_profile or {}).get('SerialNo') or None
            execute_db("UPDATE Users SET FullName=?, Role=?, IsActive=?, CanEnterMarks=? WHERE UserID=?", (data.get('FullName'), role, 1 if data.get('IsActive',1) else 0, 1 if data.get('CanEnterMarks',1) else 0, uid))
            execute_db("DELETE FROM TeacherProfiles WHERE UserID=?", (uid,))
            execute_db("DELETE FROM TeacherGrades WHERE UserID=?", (uid,))
            execute_db("DELETE FROM TeacherSubjects WHERE UserID=?", (uid,))
            if role=='teacher':
                if not teacher_serial:
                    teacher_serial = next_teacher_serial()
                execute_db("INSERT INTO TeacherProfiles (UserID,NationalID,Phone,SerialNo,Subject,Notes) VALUES (?,?,?,?,?,?)", (uid,data.get('NationalID'),data.get('Phone'),teacher_serial,data.get('Subject'),data.get('Notes')))
                save_teacher_subjects(uid, data.get('Subjects') or ([data.get('Subject')] if data.get('Subject') else []))
                for item in data.get('Grades',[]):
                    execute_db("INSERT OR IGNORE INTO TeacherGrades (UserID,GradeID,Section) VALUES (?,?,?)", (uid,item.get('GradeID'),item.get('Section') or None))
            self.send_json({"message":"تم تحديث المستخدم", "SerialNo": teacher_serial if role=='teacher' else None})

        elif path.startswith('/api/grades/'):
            if user.get('Role') != 'admin': self.send_json({"error":"للمدير فقط"},403); return
            gid = path.split('/')[3]
            name = (data.get('GradeName') or '').strip()
            if not name:
                self.send_json({"error": "يرجى إدخال اسم الصف"}, 400)
                return
            execute_db("UPDATE Grades SET GradeName=? WHERE GradeID=?", (name, gid))
            self.send_json({"message": "تم تعديل اسم الصف بنجاح"})

        elif path.startswith('/api/students/') and path.endswith('/monthly-fee'):
            sid = path.split('/')[3]
            amount = data.get('MonthlyFee')
            if amount is None or amount == '':
                self.send_json({"error": "يرجى إدخال مبلغ الرسم الشهري"}, 400)
                return
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                self.send_json({"error": "مبلغ غير صحيح"}, 400)
                return
            execute_db("UPDATE Students SET MonthlyFee=? WHERE StudentID=?", (amount, sid))
            self.send_json({"message": "تم تحديث الرسم الشهري بنجاح. سيُطبَّق المبلغ الجديد على الفواتير الشهرية القادمة فقط"})

        elif path.startswith('/api/students/'):
            if user.get('Role') != 'admin':
                self.send_json({"error": "تعديل بيانات الطلاب للمدير فقط"}, 403); return
            sid = path.split('/')[3]
            validation_errors = validate_student_data(data)
            if validation_errors:
                self.send_json({"error": " / ".join(validation_errors)}, 400)
                return
            if data.get('NationalID'):
                existing = query_db(
                    "SELECT StudentID FROM Students WHERE NationalID=? AND StudentID!=?",
                    (data['NationalID'], sid)
                )
                if existing:
                    self.send_json({"error": "رقم الهوية مكرر"}, 400)
                    return
            execute_db("""
                UPDATE Students SET FullName=?, GenderID=?, BirthDate=?,
                    NationalID=?, Mobile1=?, Mobile2=?, Address=?,
                    StatusID=?, GovID=?, Notes=?, MonthlyFee=?, Photo=COALESCE(?, Photo)
                WHERE StudentID=?
            """, (
                data.get('FullName'), data.get('GenderID'), data.get('BirthDate'),
                data.get('NationalID'), data.get('Mobile1'), data.get('Mobile2'),
                data.get('Address'), data.get('StatusID'), data.get('GovID'),
                data.get('Notes'), data.get('MonthlyFee', 0), data.get('Photo'), sid
            ))
            if data.get('GradeID'):
                existing_e = query_db("SELECT EnrollID FROM Enrollments WHERE StudentID=?", (sid,), one=True)
                if existing_e:
                    execute_db("UPDATE Enrollments SET GradeID=?, Section=?, AcademicYear=? WHERE StudentID=?",
                               (data.get('GradeID'), data.get('Section', 'أ'), data.get('AcademicYear', '2026-2027'), sid))
                else:
                    execute_db("INSERT INTO Enrollments (StudentID,GradeID,Section,AcademicYear) VALUES (?,?,?,?)",
                               (sid, data.get('GradeID'), data.get('Section', 'أ'), data.get('AcademicYear', '2026-2027')))
            self.send_json({"message": "تم تحديث بيانات الطالب"})

        elif path.startswith('/api/fees/') and len(path.split('/')) == 4:
            fid = path.split('/')[3]
            new_amount = data.get('Amount')
            fee_type = data.get('FeeType')
            if new_amount is None:
                self.send_json({"error": "يرجى إدخال المبلغ"}, 400)
                return
            execute_db("""
                UPDATE Fees SET Amount=?, FeeType=COALESCE(?,FeeType) WHERE FeeID=?
            """, (new_amount, fee_type, fid))
            self.send_json({"message": "تم تعديل مبلغ الرسوم"})

        elif path.startswith('/api/monthly-fees/') and len(path.split('/')) == 4:
            fid = path.split('/')[3]
            new_amount = data.get('Amount')
            new_paid = data.get('AmountPaid')
            if new_amount is None and new_paid is None:
                self.send_json({"error": "يرجى إدخال المبلغ"}, 400)
                return
            fee = query_db("SELECT * FROM MonthlyFees WHERE MonthlyFeeID=?", (fid,), one=True)
            if not fee:
                self.send_json({"error": "فاتورة الشهر غير موجودة"}, 404)
                return
            execute_db("""
                UPDATE MonthlyFees SET Amount=COALESCE(?,Amount), AmountPaid=COALESCE(?,AmountPaid) WHERE MonthlyFeeID=?
            """, (new_amount, new_paid, fid))
            # أعد حساب ترحيل المتبقي لكل الأشهر اللاحقة لنفس الطالب
            regenerate_carry_over_from(fee['StudentID'], fee['Year'], fee['Month'])
            self.send_json({"message": "تم تعديل فاتورة الشهر، وتم تحديث ترحيل الأشهر اللاحقة"})

        elif path.startswith('/api/parents/'):
            pid = path.split('/')[3]
            execute_db("""
                UPDATE Parents SET ParentName=?, Relation=?, Mobile=?, Job=?, NationalID=?
                WHERE ParentID=?
            """, (
                data.get('ParentName'), data.get('Relation'), data.get('Mobile'),
                data.get('Job'), data.get('NationalID'), pid
            ))
            self.send_json({"message": "تم تحديث بيانات ولي الأمر"})

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        user = self.require_auth()
        if not user: return
        if user.get('Role') != 'admin':
            self.send_json({"error":"الحذف للمدير فقط"}, 403); return

        if path.startswith('/api/backup/'):
            fname = os.path.basename(path.split('/')[3])
            fpath = os.path.join(BACKUP_DIR, fname)
            if not fname or not os.path.isfile(fpath):
                self.send_json({"error": "الملف غير موجود"}, 404); return
            os.remove(fpath)
            self.send_json({"message": "تم حذف النسخة الاحتياطية"})

        elif path.startswith('/api/subjects/'):
            sid=path.split('/')[3]
            execute_db("DELETE FROM Subjects WHERE SubjectID=?", (sid,))
            self.send_json({"message":"تم حذف المادة"})

        elif path.startswith('/api/users/'):
            uid = path.split('/')[3]
            if str(uid) == str(user['UserID']):
                self.send_json({"error":"لا يمكن حذف حسابك الحالي"}, 400); return
            execute_db("DELETE FROM Users WHERE UserID=?", (uid,))
            self.send_json({"message":"تم حذف المستخدم"})

        elif path.startswith('/api/grades/'):
            gid = path.split('/')[3]
            count = query_db("""
                SELECT COUNT(DISTINCT StudentID) AS c FROM (
                    SELECT StudentID FROM Enrollments WHERE GradeID=?
                    UNION ALL
                    SELECT StudentID FROM StudentClassMemberships WHERE GradeID=?
                )
            """, (gid, gid), one=True)['c']
            if count > 0:
                self.send_json({"error": f"لا يمكن حذف هذا الصف لوجود {count} طالب مسجل فيه. يرجى نقلهم لصف آخر أولاً"}, 400)
                return
            execute_db("DELETE FROM Grades WHERE GradeID=?", (gid,))
            self.send_json({"message": "تم حذف الصف بنجاح"})

        elif path.startswith('/api/class-memberships/'):
            membership_id = path.split('/')[3]
            row = query_db("SELECT MembershipID FROM StudentClassMemberships WHERE MembershipID=?", (membership_id,), one=True)
            if not row:
                self.send_json({"error": "العضوية الإضافية غير موجودة"}, 404); return
            execute_db("DELETE FROM StudentClassMemberships WHERE MembershipID=?", (membership_id,))
            self.send_json({"message": "تم حذف العضوية الدراسية الإضافية"})

        elif path.startswith('/api/students/') and len(path.split('/')) == 4:
            sid = path.split('/')[3]
            student = query_db("SELECT IsArchived FROM Students WHERE StudentID=?", (sid,), one=True)
            if not student:
                self.send_json({"error": "الطالب غير موجود"}, 404); return
            if not student.get('IsArchived'):
                self.send_json({
                    "error": "لا يمكن حذف طالب نهائياً قبل أرشفته أولاً. يرجى أرشفة الطالب ثم حذفه من قائمة الأرشيف إذا لزم الأمر."
                }, 400)
                return
            execute_db("DELETE FROM Students WHERE StudentID=?", (sid,))
            self.send_json({"message": "تم حذف الطالب نهائياً"})

        elif path.startswith('/api/students/') and path.endswith('/parent'):
            # إلغاء ربط ولي الأمر عن هذا الطالب فقط، دون حذف سجل ولي الأمر نفسه
            # (لأنه قد يكون مرتبطاً بإخوة آخرين لهذا الطالب)
            sid = path.split('/')[3]
            execute_db("DELETE FROM ParentStudents WHERE StudentID=?", (sid,))
            self.send_json({"message": "تم إلغاء ربط ولي الأمر عن هذا الطالب"})

        elif path.startswith('/api/parents/'):
            # حذف سجل ولي الأمر بالكامل (سيُلغى ارتباطه بجميع الطلاب المرتبطين به تلقائياً)
            pid = path.split('/')[3]
            execute_db("DELETE FROM Parents WHERE ParentID=?", (pid,))
            self.send_json({"message": "تم حذف ولي الأمر"})

        elif path.startswith('/api/fees/'):
            fid = path.split('/')[3]
            execute_db("DELETE FROM Fees WHERE FeeID=?", (fid,))
            self.send_json({"message": "تم حذف الرسم"})

        else:
            self.send_json({"error": "Not found"}, 404)


def get_main_html():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, encoding='utf-8') as f:
        return f.read()


def run(port=None, host=None):
    port = int(port if port is not None else os.environ.get('AFAQ_PORT', '8000'))
    host = host or os.environ.get('AFAQ_HOST', '127.0.0.1')
    init_database()
    server = HTTPServer((host, port), APIHandler)
    display_host = 'localhost' if host in ('127.0.0.1', 'localhost') else host
    print(f"✅ النظام يعمل على: http://{display_host}:{port}")
    print("   اضغط Ctrl+C للإيقاف")
    server.serve_forever()


if __name__ == '__main__':
    run()

# ARCHIVE_REASON_NOTE_PATCH_V1

# STUDENT_PAYMENT_STATUS_FILTER_PATCH_V1
