"""
نظام إدارة الطلاب - تهيئة قاعدة البيانات
"""
import sqlite3
import os
import hashlib
import secrets
from datetime import datetime


DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "students.db")
DB_PATH = os.path.abspath(os.environ.get("AFAQ_DB_PATH", DEFAULT_DB_PATH))


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_database():
    conn = get_connection()
    c = conn.cursor()
    if os.environ.get("AFAQ_SQLITE_WAL", "0").strip().lower() in ("1", "true", "yes", "on"):
        c.execute("PRAGMA journal_mode = WAL")

    # جدول الجنس
    c.execute("""
        CREATE TABLE IF NOT EXISTS Gender (
            GenderID INTEGER PRIMARY KEY,
            GenderName TEXT NOT NULL
        )
    """)

    # جدول الحالة
    c.execute("""
        CREATE TABLE IF NOT EXISTS Status (
            StatusID INTEGER PRIMARY KEY,
            StatusName TEXT NOT NULL
        )
    """)

    # جدول المحافظات
    c.execute("""
        CREATE TABLE IF NOT EXISTS Governorates (
            GovID INTEGER PRIMARY KEY AUTOINCREMENT,
            GovName TEXT NOT NULL
        )
    """)

    # جدول المدارس
    c.execute("""
        CREATE TABLE IF NOT EXISTS Schools (
            SchoolID INTEGER PRIMARY KEY AUTOINCREMENT,
            SchoolName TEXT NOT NULL,
            GovID INTEGER,
            Region TEXT,
            Phone TEXT,
            FOREIGN KEY (GovID) REFERENCES Governorates(GovID)
        )
    """)

    # جدول الصفوف الدراسية
    c.execute("""
        CREATE TABLE IF NOT EXISTS Grades (
            GradeID INTEGER PRIMARY KEY,
            GradeName TEXT NOT NULL
        )
    """)

    # جدول الطلاب الرئيسي
    c.execute("""
        CREATE TABLE IF NOT EXISTS Students (
            StudentID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentNo TEXT NOT NULL UNIQUE,
            NationalID TEXT UNIQUE,
            FullName TEXT NOT NULL,
            GenderID INTEGER,
            BirthDate TEXT,
            Mobile1 TEXT,
            Mobile2 TEXT,
            Address TEXT,
            StatusID INTEGER DEFAULT 1,
            GovID INTEGER,
            Notes TEXT,
            Photo BLOB,
            RegDate TEXT DEFAULT (date('now')),
            MonthlyFee REAL DEFAULT 60,
            FOREIGN KEY (GenderID) REFERENCES Gender(GenderID),
            FOREIGN KEY (StatusID) REFERENCES Status(StatusID),
            FOREIGN KEY (GovID) REFERENCES Governorates(GovID)
        )
    """)

    # جدول أولياء الأمور: كيان مستقل (شخص) لا يحمل StudentID مباشرة.
    # العلاقة الفعلية بين ولي الأمر والطالب تُدار حصراً عبر جدول الربط ParentStudents أدناه.
    c.execute("""
        CREATE TABLE IF NOT EXISTS Parents (
            ParentID INTEGER PRIMARY KEY AUTOINCREMENT,
            ParentName TEXT NOT NULL,
            Relation TEXT,
            Mobile TEXT,
            Job TEXT,
            NationalID TEXT
        )
    """)

    # جدول الربط بين ولي الأمر والطلاب — هو مصدر العلاقة الوحيد والملزم:
    # - StudentID عمود فريد (UNIQUE) => كل طالب لا يمكن أن يرتبط إلا بولي أمر واحد فقط
    # - ParentID غير فريد => نفس ولي الأمر يمكن أن يرتبط بأكثر من طالب (إخوة)
    c.execute("""CREATE TABLE IF NOT EXISTS ParentStudents (
        ParentID INTEGER NOT NULL,
        StudentID INTEGER NOT NULL UNIQUE,
        PRIMARY KEY (ParentID, StudentID),
        FOREIGN KEY (ParentID) REFERENCES Parents(ParentID) ON DELETE CASCADE,
        FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
    )""")

    # جدول الحالة الاجتماعية للطالب (يتيم الأب / الأم / الأبوين، أبوين منفصلين، مغترب، أسير ... إلخ)
    # تم الاحتفاظ باسم الجدول والأعمدة القديمة (Orphans / OrphanType) للحفاظ على توافق البيانات القديمة
    c.execute("""
        CREATE TABLE IF NOT EXISTS Orphans (
            OrphanID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL UNIQUE,
            OrphanType TEXT,
            OrphanDate TEXT,
            SupportedBy TEXT,
            Notes TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )
    """)

    # جدول الحالة الصحية للطالب (سليم / مرض مزمن / إعاقة حركية ... إلخ)
    c.execute("""
        CREATE TABLE IF NOT EXISTS HealthStatus (
            HealthStatusID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL UNIQUE,
            HealthType TEXT,
            Notes TEXT,
            UpdatedDate TEXT DEFAULT (date('now')),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )
    """)

    # جدول تصنيفات الحالة المادية (5 تصنيفات ثابتة بأسماء قابلة للتعديل)
    c.execute("""
        CREATE TABLE IF NOT EXISTS FinancialCategories (
            FinCategoryID INTEGER PRIMARY KEY AUTOINCREMENT,
            CategoryName TEXT NOT NULL
        )
    """)

    # جدول الحالة المادية للطالب (مرتبط بأحد التصنيفات الخمسة أعلاه)
    c.execute("""
        CREATE TABLE IF NOT EXISTS FinancialStatus (
            FinancialStatusID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL UNIQUE,
            FinCategoryID INTEGER,
            Notes TEXT,
            UpdatedDate TEXT DEFAULT (date('now')),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            FOREIGN KEY (FinCategoryID) REFERENCES FinancialCategories(FinCategoryID)
        )
    """)

    # جدول تسجيل الطالب في المدرسة
    c.execute("""
        CREATE TABLE IF NOT EXISTS Enrollments (
            EnrollID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            SchoolID INTEGER,
            GradeID INTEGER,
            Section TEXT,
            AcademicYear TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            FOREIGN KEY (SchoolID) REFERENCES Schools(SchoolID),
            FOREIGN KEY (GradeID) REFERENCES Grades(GradeID)
        )
    """)

    # جدول المساعدات
    c.execute("""
        CREATE TABLE IF NOT EXISTS Aids (
            AidID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            AidType TEXT,
            Amount REAL,
            AidDate TEXT,
            GivenBy TEXT,
            Notes TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )
    """)

    # جدول إعدادات عامة للنظام (مفتاح/قيمة) - يُستخدم لتتبّع عمليات الترقية التي تُنفَّذ مرة واحدة فقط
    c.execute("""
        CREATE TABLE IF NOT EXISTS AppSettings (
            SettingKey TEXT PRIMARY KEY,
            SettingValue TEXT
        )
    """)

    # جدول الرسوم الإضافية (رسوم لمرة واحدة: نشاط، تسجيل، وغيرها - منفصلة عن الرسم الشهري)
    c.execute("""
        CREATE TABLE IF NOT EXISTS Fees (
            FeeID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            FeeType TEXT DEFAULT 'رسوم دراسية',
            Amount REAL NOT NULL,
            AmountPaid REAL DEFAULT 0,
            DueDate TEXT,
            PaidDate TEXT,
            AcademicYear TEXT,
            Notes TEXT,
            Photo TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )
    """)

    # جدول الرسوم الشهرية (تُولَّد تلقائياً كل شهر، مع ترحيل المتبقي من الشهر السابق)
    c.execute("""
        CREATE TABLE IF NOT EXISTS MonthlyFees (
            MonthlyFeeID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            Year INTEGER NOT NULL,
            Month INTEGER NOT NULL,
            Amount REAL NOT NULL DEFAULT 0,
            AmountPaid REAL DEFAULT 0,
            PaidDate TEXT,
            Notes TEXT,
            Photo TEXT,
            CreatedDate TEXT DEFAULT (date('now')),
            UNIQUE(StudentID, Year, Month),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )
    """)

    # جدول إيصالات الدفع (يخدم الرسوم الشهرية والرسوم الإضافية معاً عبر FeeKind)
    # FeeKind = 'monthly' يشير إلى MonthlyFees.MonthlyFeeID ، و 'extra' يشير إلى Fees.FeeID
    c.execute("""
        CREATE TABLE IF NOT EXISTS FeeReceipts (
            ReceiptID INTEGER PRIMARY KEY AUTOINCREMENT,
            FeeID INTEGER NOT NULL,
            FeeKind TEXT NOT NULL DEFAULT 'extra',
            StudentID INTEGER NOT NULL,
            Amount REAL NOT NULL,
            PaidDate TEXT,
            ReceivedBy TEXT,
            Notes TEXT,
            Photo TEXT,
            PaymentMethod TEXT DEFAULT 'كاش',
            IsPrinted INTEGER NOT NULL DEFAULT 0,
            PrintedAt TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )
    """)


    # جدول المستخدمين والصلاحيات
    c.execute("""
        CREATE TABLE IF NOT EXISTS Users (
            UserID INTEGER PRIMARY KEY AUTOINCREMENT,
            Username TEXT NOT NULL UNIQUE,
            PasswordHash TEXT NOT NULL,
            FullName TEXT NOT NULL,
            Role TEXT NOT NULL DEFAULT 'teacher',
            IsActive INTEGER DEFAULT 1,
            CanEnterMarks INTEGER NOT NULL DEFAULT 1,
            LastLogin TEXT,
            CreatedAt TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # بيانات المعلمين التي يدخلها المدير
    c.execute("""
        CREATE TABLE IF NOT EXISTS TeacherProfiles (
            TeacherID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL UNIQUE,
            NationalID TEXT,
            Phone TEXT,
            SerialNo TEXT,
            Subject TEXT,
            Notes TEXT,
            FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        )
    """)

    # الصفوف المسموح للمعلم بمشاهدة طلابها
    c.execute("""
        CREATE TABLE IF NOT EXISTS TeacherGrades (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            GradeID INTEGER NOT NULL,
            Section TEXT,
            FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE,
            FOREIGN KEY (GradeID) REFERENCES Grades(GradeID),
            UNIQUE(UserID, GradeID, Section)
        )
    """)

    # جدول علامات الطلاب حسب معلم المادة
    c.execute("""
        CREATE TABLE IF NOT EXISTS StudentMarks (
            MarkID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            TeacherUserID INTEGER NOT NULL,
            Subject TEXT NOT NULL,
            ExamName TEXT DEFAULT 'علامة',
            Mark REAL,
            MaxMark REAL DEFAULT 100,
            Notes TEXT,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            UpdatedAt TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            FOREIGN KEY (TeacherUserID) REFERENCES Users(UserID) ON DELETE CASCADE,
            UNIQUE(StudentID, TeacherUserID, Subject, ExamName)
        )
    """)



    # جدول المواد الدراسية
    c.execute("""
        CREATE TABLE IF NOT EXISTS Subjects (
            SubjectID INTEGER PRIMARY KEY AUTOINCREMENT,
            SubjectName TEXT NOT NULL UNIQUE,
            Notes TEXT
        )
    """)

    # ربط المعلم بالمواد التي يدرسها
    c.execute("""
        CREATE TABLE IF NOT EXISTS TeacherSubjects (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            SubjectID INTEGER NOT NULL,
            FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE,
            FOREIGN KEY (SubjectID) REFERENCES Subjects(SubjectID) ON DELETE CASCADE,
            UNIQUE(UserID, SubjectID)
        )
    """)



    # ربط كل صف بمواده الدراسية
    c.execute("""
        CREATE TABLE IF NOT EXISTS GradeSubjects (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            GradeID INTEGER NOT NULL,
            SubjectID INTEGER NOT NULL,
            FOREIGN KEY (GradeID) REFERENCES Grades(GradeID) ON DELETE CASCADE,
            FOREIGN KEY (SubjectID) REFERENCES Subjects(SubjectID) ON DELETE CASCADE,
            UNIQUE(GradeID, SubjectID)
        )
    """)

    # جدول الحضور والغياب اليومي للطلاب
    c.execute("""
        CREATE TABLE IF NOT EXISTS Attendance (
            AttendanceID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            AttendanceDate TEXT NOT NULL,
            Status TEXT NOT NULL DEFAULT 'حاضر',
            Notes TEXT,
            RecordedBy INTEGER,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            UpdatedAt TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            FOREIGN KEY (RecordedBy) REFERENCES Users(UserID),
            UNIQUE(StudentID, AttendanceDate)
        )
    """)

    # مشروع التخرج الخاص بكل طالب في نهاية الفصل
    c.execute("""
        CREATE TABLE IF NOT EXISTS GraduationProjects (
            ProjectID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL UNIQUE,
            ProjectTitle TEXT,
            Description TEXT,
            Supervisor TEXT,
            ProjectStatus TEXT NOT NULL DEFAULT 'لم يبدأ',
            ProjectGrade REAL,
            SubmissionDate TEXT,
            Notes TEXT,
            UpdatedBy INTEGER,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            UpdatedAt TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            FOREIGN KEY (UpdatedBy) REFERENCES Users(UserID)
        )
    """)

    # سجل نشاطات المستخدمين
    c.execute("""
        CREATE TABLE IF NOT EXISTS ActivityLogs (
            ActivityID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER,
            Username TEXT,
            FullName TEXT,
            Action TEXT NOT NULL,
            Method TEXT,
            Path TEXT,
            Details TEXT,
            CreatedAt TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_user_date ON ActivityLogs(UserID, CreatedAt DESC)")

    # جلسات تسجيل الدخول
    c.execute("""
        CREATE TABLE IF NOT EXISTS UserSessions (
            Token TEXT PRIMARY KEY,
            UserID INTEGER NOT NULL,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            ExpiresAt TEXT,
            FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        )
    """)

    # العضويات الدراسية الإضافية. يبقى جدول Enrollments هو الصف الأساسي للطالب
    # حتى لا تتكرر السجلات المالية، بينما يسمح هذا الجدول بانضمامه إلى صفوف أو
    # مجموعات إضافية (مثل مجموعات اللغة الإنجليزية) بصورة مستقلة.
    c.execute("""
        CREATE TABLE IF NOT EXISTS StudentClassMemberships (
            MembershipID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            GradeID INTEGER NOT NULL,
            Section TEXT NOT NULL DEFAULT 'أ',
            AcademicYear TEXT NOT NULL DEFAULT '2026-2027',
            Notes TEXT,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            FOREIGN KEY (GradeID) REFERENCES Grades(GradeID) ON DELETE CASCADE,
            UNIQUE(StudentID, GradeID, Section, AcademicYear)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_memberships_grade ON StudentClassMemberships(GradeID, Section)")

    # ديون نقدية منفصلة عن الرسوم الدراسية، مع سجل دفعات تراكمي لكل دين.
    c.execute("""
        CREATE TABLE IF NOT EXISTS CashDebts (
            DebtID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            Description TEXT NOT NULL,
            PrincipalAmount REAL NOT NULL CHECK(PrincipalAmount > 0),
            DebtDate TEXT NOT NULL DEFAULT (date('now')),
            DueDate TEXT,
            Notes TEXT,
            CreatedBy INTEGER,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            FOREIGN KEY (CreatedBy) REFERENCES Users(UserID)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_cash_debts_student ON CashDebts(StudentID, DebtDate DESC)")
    c.execute("""
        CREATE TABLE IF NOT EXISTS CashDebtPayments (
            PaymentID INTEGER PRIMARY KEY AUTOINCREMENT,
            DebtID INTEGER NOT NULL,
            Amount REAL NOT NULL CHECK(Amount > 0),
            PaymentDate TEXT NOT NULL DEFAULT (date('now')),
            ReceivedBy TEXT,
            Notes TEXT,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (DebtID) REFERENCES CashDebts(DebtID) ON DELETE CASCADE
        )
    """)

    # حملات تواصل يدوية فقط. لا يوجد في هذا النظام أي موصل إرسال تلقائي.
    c.execute("""
        CREATE TABLE IF NOT EXISTS CommunicationCampaigns (
            CampaignID INTEGER PRIMARY KEY AUTOINCREMENT,
            Title TEXT NOT NULL,
            MessageBody TEXT NOT NULL,
            Status TEXT NOT NULL DEFAULT 'confirmed_manual',
            RecipientCount INTEGER NOT NULL DEFAULT 0,
            CreatedBy INTEGER,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            ConfirmedAt TEXT,
            FOREIGN KEY (CreatedBy) REFERENCES Users(UserID)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS CommunicationRecipients (
            RecipientID INTEGER PRIMARY KEY AUTOINCREMENT,
            CampaignID INTEGER NOT NULL,
            StudentID INTEGER NOT NULL,
            Mobile TEXT NOT NULL,
            DeliveryStatus TEXT NOT NULL DEFAULT 'جاهز للإرسال اليدوي',
            FOREIGN KEY (CampaignID) REFERENCES CommunicationCampaigns(CampaignID) ON DELETE CASCADE,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            UNIQUE(CampaignID, StudentID)
        )
    """)

    # إدخال البيانات الثابتة
    c.execute("SELECT COUNT(*) FROM Gender")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO Gender VALUES (?,?)", [
            (1, "ذكر"), (2, "أنثى")
        ])

    c.execute("SELECT COUNT(*) FROM Status")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO Status VALUES (?,?)", [
            (1, "نشط"), (2, "متخرج"), (3, "منسحب"), (4, "موقوف")
        ])

    c.execute("SELECT COUNT(*) FROM FinancialCategories")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO FinancialCategories (CategoryName) VALUES (?)", [
            ("ميسورة الحال",), ("متوسطة الحال",), ("دون المتوسط",),
            ("فقيرة",), ("فقيرة جداً",)
        ])

    c.execute("SELECT COUNT(*) FROM Grades")
    if c.fetchone()[0] == 0:
        grades = [
            (-3, "مستمع"), (-2, "بستان"), (-1, "تمهيدي"),
            (1, "الأول"), (2, "الثاني"), (3, "الثالث"),
            (4, "الرابع"), (5, "الخامس"), (6, "السادس"),
            (7, "السابع"), (8, "الثامن"), (9, "التاسع"),
            (10, "العاشر"), (11, "الحادي عشر"), (12, "الثاني عشر"),
        ]
        c.executemany("INSERT INTO Grades VALUES (?,?)", grades)

    c.execute("SELECT COUNT(*) FROM Governorates")
    if c.fetchone()[0] == 0:
        govs = [
            ("نابلس",), ("رام الله",), ("الخليل",), ("بيت لحم",),
            ("جنين",), ("طولكرم",), ("قلقيلية",), ("سلفيت",),
            ("أريحا",), ("طوباس",), ("القدس",), ("غزة",),
        ]
        c.executemany("INSERT INTO Governorates (GovName) VALUES (?)", govs)

    # ===== ترقية تلقائية لقواعد البيانات القديمة =====
    # إذا كانت قاعدة بيانات قديمة (تم إنشاؤها قبل إضافة حقل الرسم الشهري)
    # نضيف العمود تلقائياً دون أي فقدان للبيانات الموجودة
    user_cols = {row[1] for row in c.execute("PRAGMA table_info(Users)").fetchall()}
    if "CanEnterMarks" not in user_cols:
        c.execute("ALTER TABLE Users ADD COLUMN CanEnterMarks INTEGER NOT NULL DEFAULT 1")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة صلاحية إدخال العلامات لكل مستخدم")

    # ===== الرقم التسلسلي الموحّد للمعلمين =====
    # تُرقّم حسابات المعلمين تلقائياً بصيغة 0001، 0002، ... ولا يعتمد النظام
    # على أي قيمة يكتبها المستخدم يدوياً. عند أول تشغيل لهذه النسخة تتم إعادة
    # ترتيب أرقام المعلمين الموجودين حسب تاريخ/ترتيب إضافتهم، ثم تُحفظ علامة
    # ترقية حتى لا تتغير أرقامهم في التشغيلات اللاحقة.
    teacher_serial_flag = c.execute(
        "SELECT SettingValue FROM AppSettings WHERE SettingKey='teacher_serials_v32_done'"
    ).fetchone()
    if not teacher_serial_flag:
        teacher_rows = c.execute("""
            SELECT tp.TeacherID, tp.UserID
            FROM TeacherProfiles tp
            JOIN Users u ON u.UserID=tp.UserID
            WHERE u.Role='teacher'
            ORDER BY u.UserID, tp.TeacherID
        """).fetchall()
        for serial_index, teacher_row in enumerate(teacher_rows, start=1):
            c.execute(
                "UPDATE TeacherProfiles SET SerialNo=? WHERE TeacherID=?",
                (f"{serial_index:04d}", teacher_row[0])
            )
        c.execute(
            "INSERT OR REPLACE INTO AppSettings (SettingKey, SettingValue) "
            "VALUES ('teacher_serials_v32_done', datetime('now','localtime'))"
        )
        if teacher_rows:
            print("🔄 تمت ترقية قاعدة البيانات: تم توحيد الأرقام التسلسلية للمعلمين تلقائياً")

    # ضمان منح رقم تلقائي لأي ملف معلم قديم ناقص، دون تغيير أرقام المعلمين الحالية.
    serial_rows = c.execute(
        "SELECT SerialNo FROM TeacherProfiles WHERE SerialNo IS NOT NULL AND TRIM(SerialNo)!=''"
    ).fetchall()
    used_serial_numbers = []
    for serial_row in serial_rows:
        digits = ''.join(ch for ch in str(serial_row[0]) if ch.isdigit())
        if digits:
            used_serial_numbers.append(int(digits))
    next_serial_number = (max(used_serial_numbers) if used_serial_numbers else 0) + 1
    missing_serial_rows = c.execute("""
        SELECT tp.TeacherID
        FROM TeacherProfiles tp
        JOIN Users u ON u.UserID=tp.UserID
        WHERE u.Role='teacher' AND (tp.SerialNo IS NULL OR TRIM(tp.SerialNo)='')
        ORDER BY u.UserID, tp.TeacherID
    """).fetchall()
    for teacher_row in missing_serial_rows:
        c.execute(
            "UPDATE TeacherProfiles SET SerialNo=? WHERE TeacherID=?",
            (f"{next_serial_number:04d}", teacher_row[0])
        )
        next_serial_number += 1

    # يمنع تكرار الرقم التسلسلي بين ملفي معلمين مختلفين.
    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_teacherprofiles_serial_unique
        ON TeacherProfiles(SerialNo)
        WHERE SerialNo IS NOT NULL AND TRIM(SerialNo)!=''
    """)

    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(Students)").fetchall()}
    if "Photo" not in existing_cols:
        c.execute("ALTER TABLE Students ADD COLUMN Photo TEXT")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حقل (صورة الطالب)")
    if "MonthlyFee" not in existing_cols:
        c.execute("ALTER TABLE Students ADD COLUMN MonthlyFee REAL DEFAULT 60")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حقل (الرسم الشهري المطلوب)")
    if "IsArchived" not in existing_cols:
        c.execute("ALTER TABLE Students ADD COLUMN IsArchived INTEGER NOT NULL DEFAULT 0")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حقل (أرشفة الطالب) بدلاً من الحذف النهائي")
    if "ArchivedDate" not in existing_cols:
        c.execute("ALTER TABLE Students ADD COLUMN ArchivedDate TEXT")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حقل (تاريخ الأرشفة)")
    if "ArchiveReason" not in existing_cols:
        c.execute("ALTER TABLE Students ADD COLUMN ArchiveReason TEXT")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حقل (سبب أرشفة الطالب)")
    if "ArchiveNote" not in existing_cols:
        c.execute("ALTER TABLE Students ADD COLUMN ArchiveNote TEXT")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حقل (ملاحظة أرشفة الطالب)")

    # إضافة صفوف الروضة (مستمع/بستان/تمهيدي) لقواعد البيانات القديمة التي لا تحتوي عليها
    existing_grade_ids = {row[0] for row in c.execute("SELECT GradeID FROM Grades").fetchall()}
    kindergarten_grades = [(-3, "مستمع"), (-2, "بستان"), (-1, "تمهيدي")]
    missing_kg = [g for g in kindergarten_grades if g[0] not in existing_grade_ids]
    if missing_kg:
        c.executemany("INSERT INTO Grades VALUES (?,?)", missing_kg)
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة صفوف الروضة (مستمع/بستان/تمهيدي)")

    # ترقية قواعد بيانات قديمة: جدول Parents كان يحمل عمود StudentID مباشرة، أي أن كل طالب
    # كان يحصل على سجل ولي أمر منفصل حتى لو كان إخوته يشتركون بنفس ولي الأمر فعلياً. نطبّق هنا
    # العلاقة الصحيحة: كل طالب له ولي أمر واحد فقط، وولي الأمر نفسه قد يرتبط بأكثر من طالب،
    # وذلك بدمج السجلات القديمة التي تتطابق في (الاسم ورقم الجوال) أو (رقم الهوية) في سجل واحد،
    # مع نقل الارتباطات جميعها إلى جدول الربط ParentStudents.
    existing_parent_cols = {row[1] for row in c.execute("PRAGMA table_info(Parents)").fetchall()}
    if "StudentID" in existing_parent_cols:
        old_parents = c.execute(
            "SELECT ParentID, StudentID, ParentName, Relation, Mobile, Job, NationalID FROM Parents"
        ).fetchall()
        c.execute("PRAGMA foreign_keys = OFF")
        c.execute("ALTER TABLE Parents RENAME TO Parents_old")
        # ملاحظة: إعادة تسمية Parents تجعل SQLite يحدّث تلقائياً مرجع FOREIGN KEY في تعريف
        # ParentStudents ليشير إلى "Parents_old"، لذا يجب إعادة بناء ParentStudents أيضاً
        # حتى يشير مرجعها لجدول Parents الجديد الصحيح بعد إنشائه.
        c.execute("DROP TABLE IF EXISTS ParentStudents")
        c.execute("""
            CREATE TABLE Parents (
                ParentID INTEGER PRIMARY KEY AUTOINCREMENT,
                ParentName TEXT NOT NULL,
                Relation TEXT,
                Mobile TEXT,
                Job TEXT,
                NationalID TEXT
            )
        """)
        c.execute("""CREATE TABLE ParentStudents (
            ParentID INTEGER NOT NULL,
            StudentID INTEGER NOT NULL UNIQUE,
            PRIMARY KEY (ParentID, StudentID),
            FOREIGN KEY (ParentID) REFERENCES Parents(ParentID) ON DELETE CASCADE,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )""")
        identity_map = {}  # (ParentName, Mobile, NationalID) -> ParentID الجديد بعد الدمج
        for row in old_parents:
            _, old_student_id, pname, relation, mobile, job, natid = row
            key = (pname or '', mobile or '', natid or '')
            if key not in identity_map:
                new_pid = c.execute(
                    "INSERT INTO Parents (ParentName, Relation, Mobile, Job, NationalID) VALUES (?,?,?,?,?)",
                    (pname, relation, mobile, job, natid)
                ).lastrowid
                identity_map[key] = new_pid
            new_pid = identity_map[key]
            if old_student_id is not None:
                # كل طالب له ولي أمر واحد فقط: نتجاهل أي تكرار سابق لنفس الطالب بفضل UNIQUE على StudentID
                c.execute(
                    "INSERT OR IGNORE INTO ParentStudents (ParentID, StudentID) VALUES (?,?)",
                    (new_pid, old_student_id)
                )
        c.execute("DROP TABLE Parents_old")
        c.execute("PRAGMA foreign_keys = ON")
        print("🔄 تمت ترقية قاعدة البيانات: تم تطبيق العلاقة الصحيحة بين الطلاب وأولياء الأمور "
              "(كل طالب له ولي أمر واحد، وولي الأمر الواحد قد يرتبط بأكثر من طالب) مع دمج "
              "الإخوة الذين يتشاركون نفس ولي الأمر تلقائياً في سجل واحد")

    # إصلاح قواعد بيانات تضررت من عملية ترقية سابقة: عند إعادة تسمية Parents، يحدّث SQLite
    # تلقائياً مرجع FOREIGN KEY في ParentStudents ليشير إلى الاسم القديم (Parents_old) الذي
    # يُحذف بعد ذلك، فيبقى المرجع معلّقاً (dangling) ويفشل أي إدراج/حذف في ParentStudents
    ps_fk_list = c.execute("PRAGMA foreign_key_list(ParentStudents)").fetchall()
    needs_ps_fk_fix = any(row[2] != "Parents" for row in ps_fk_list if row[3] == "ParentID") if ps_fk_list else False
    if needs_ps_fk_fix:
        c.execute("PRAGMA foreign_keys = OFF")
        old_links = c.execute("SELECT ParentID, StudentID FROM ParentStudents").fetchall()
        c.execute("DROP TABLE ParentStudents")
        c.execute("""CREATE TABLE ParentStudents (
            ParentID INTEGER NOT NULL,
            StudentID INTEGER NOT NULL UNIQUE,
            PRIMARY KEY (ParentID, StudentID),
            FOREIGN KEY (ParentID) REFERENCES Parents(ParentID) ON DELETE CASCADE,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )""")
        for pid_, sid_ in old_links:
            c.execute("INSERT OR IGNORE INTO ParentStudents (ParentID, StudentID) VALUES (?,?)", (pid_, sid_))
        c.execute("PRAGMA foreign_keys = ON")
        print("🔄 تمت ترقية قاعدة البيانات: تم إصلاح مرجع جدول ربط أولياء الأمور (ParentStudents) بعد ترقية سابقة")

    # إضافة عمود الصورة (Photo) لجدول الرسوم لقواعد البيانات القديمة - تستخدم لإرفاق صورة بسند القبض
    existing_fee_cols = {row[1] for row in c.execute("PRAGMA table_info(Fees)").fetchall()}
    if "Photo" not in existing_fee_cols:
        c.execute("ALTER TABLE Fees ADD COLUMN Photo TEXT")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حقل (صورة الإشعار) لجدول الرسوم")

    # إضافة عمود الصورة (Photo) لجدول إيصالات الدفع (FeeReceipts) لقواعد البيانات القديمة
    # تستخدم لإرفاق صورة الإشعار عند تسجيل الدفعة نفسها (منفصلة عن صورة الرسم عند إنشائه)
    existing_receipt_photo_cols = {row[1] for row in c.execute("PRAGMA table_info(FeeReceipts)").fetchall()}
    if "Photo" not in existing_receipt_photo_cols:
        c.execute("ALTER TABLE FeeReceipts ADD COLUMN Photo TEXT")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حقل (صورة الإشعار) لجدول إيصالات الدفع")

    # إصلاح قواعد بيانات قديمة كان فيها جدول FeeReceipts بدون ON DELETE CASCADE
    # (كان هذا يسبب فشل حذف الطالب بخطأ "FOREIGN KEY constraint failed" إذا كانت له إيصالات دفع)
    fk_list = c.execute("PRAGMA foreign_key_list(FeeReceipts)").fetchall()
    needs_fk_fix = any(row[2] == "Students" and row[6] != "CASCADE" for row in fk_list)
    if needs_fk_fix:
        c.execute("PRAGMA foreign_keys = OFF")
        c.execute("ALTER TABLE FeeReceipts RENAME TO FeeReceipts_old")
        c.execute("""
            CREATE TABLE FeeReceipts (
                ReceiptID INTEGER PRIMARY KEY AUTOINCREMENT,
                FeeID INTEGER NOT NULL,
                StudentID INTEGER NOT NULL,
                Amount REAL NOT NULL,
                PaidDate TEXT,
                ReceivedBy TEXT,
                Notes TEXT,
                Photo TEXT,
                PaymentMethod TEXT DEFAULT 'كاش',
                IsPrinted INTEGER NOT NULL DEFAULT 0,
                PrintedAt TEXT,
                FOREIGN KEY (FeeID) REFERENCES Fees(FeeID) ON DELETE CASCADE,
                FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
            )
        """)
        c.execute("""
            INSERT INTO FeeReceipts (ReceiptID, FeeID, StudentID, Amount, PaidDate, ReceivedBy, Notes)
            SELECT ReceiptID, FeeID, StudentID, Amount, PaidDate, ReceivedBy, Notes FROM FeeReceipts_old
        """)
        c.execute("DROP TABLE FeeReceipts_old")
        c.execute("PRAGMA foreign_keys = ON")
        print("🔄 تمت ترقية قاعدة البيانات: تم إصلاح ربط جدول إيصالات الدفع (FeeReceipts) للحذف التلقائي السليم")

    # إزالة قيد الربط (FOREIGN KEY) بين FeeReceipts.FeeID وجدول Fees فقط، لأن FeeID أصبح
    # يشير إما لجدول Fees (رسوم إضافية) أو لجدول MonthlyFees (رسوم شهرية) حسب عمود FeeKind،
    # ولا يمكن لقيد ربط واحد أن يشير لجدولين معاً. وجود هذا القيد القديم كان يسبب فشل تسجيل
    # دفعات الرسوم الشهرية بخطأ "FOREIGN KEY constraint failed"
    fk_list2 = c.execute("PRAGMA foreign_key_list(FeeReceipts)").fetchall()
    needs_feeid_fk_removal = any(row[2] == "Fees" for row in fk_list2)
    if needs_feeid_fk_removal:
        c.execute("PRAGMA foreign_keys = OFF")
        c.execute("ALTER TABLE FeeReceipts RENAME TO FeeReceipts_old2")
        c.execute("""
            CREATE TABLE FeeReceipts (
                ReceiptID INTEGER PRIMARY KEY AUTOINCREMENT,
                FeeID INTEGER NOT NULL,
                FeeKind TEXT NOT NULL DEFAULT 'extra',
                StudentID INTEGER NOT NULL,
                Amount REAL NOT NULL,
                PaidDate TEXT,
                ReceivedBy TEXT,
                Notes TEXT,
                Photo TEXT,
                PaymentMethod TEXT DEFAULT 'كاش',
                IsPrinted INTEGER NOT NULL DEFAULT 0,
                PrintedAt TEXT,
                FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
            )
        """)
        c.execute("""
            INSERT INTO FeeReceipts (ReceiptID, FeeID, FeeKind, StudentID, Amount, PaidDate, ReceivedBy, Notes)
            SELECT ReceiptID, FeeID, COALESCE(FeeKind,'extra'), StudentID, Amount, PaidDate, ReceivedBy, Notes FROM FeeReceipts_old2
        """)
        c.execute("DROP TABLE FeeReceipts_old2")
        c.execute("PRAGMA foreign_keys = ON")
        print("🔄 تمت ترقية قاعدة البيانات: تم تحديث جدول إيصالات الدفع ليدعم الرسوم الشهرية والإضافية معاً")

    # إضافة عمود FeeKind لجدول إيصالات الدفع (يميّز بين إيصال رسم شهري وإيصال رسم إضافي)
    existing_receipt_cols = {row[1] for row in c.execute("PRAGMA table_info(FeeReceipts)").fetchall()}
    if "FeeKind" not in existing_receipt_cols:
        c.execute("ALTER TABLE FeeReceipts ADD COLUMN FeeKind TEXT NOT NULL DEFAULT 'extra'")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حقل (نوع الرسم) لجدول إيصالات الدفع")
    # يبقى سجل الدفعة محفوظاً مالياً، لكن لا يُعتمد كسند قبض إلا عند تنفيذ الطباعة.
    existing_receipt_cols = {row[1] for row in c.execute("PRAGMA table_info(FeeReceipts)").fetchall()}
    if "Photo" not in existing_receipt_cols:
        c.execute("ALTER TABLE FeeReceipts ADD COLUMN Photo TEXT")
    if "PaymentMethod" not in existing_receipt_cols:
        c.execute("ALTER TABLE FeeReceipts ADD COLUMN PaymentMethod TEXT DEFAULT 'كاش'")
    if "IsPrinted" not in existing_receipt_cols:
        c.execute("ALTER TABLE FeeReceipts ADD COLUMN IsPrinted INTEGER NOT NULL DEFAULT 0")
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة حالة طباعة سند القبض")
    if "PrintedAt" not in existing_receipt_cols:
        c.execute("ALTER TABLE FeeReceipts ADD COLUMN PrintedAt TEXT")

    # ===== التحويل لنظام الرسوم الشهرية (مرة واحدة فقط) =====
    # يُحذف كل ما كان في جدول الرسوم القديم (Fees) وإيصالاته المرتبطة به، ويبدأ النظام
    # بصفحة نظيفة تماماً للرسوم الشهرية التلقائية (MonthlyFees)، حسب طلب صاحب النظام.
    # نستخدم AppSettings كعلامة لضمان أن هذا التحويل يحدث مرة واحدة فقط ولا يتكرر
    # في كل تشغيل قادم للبرنامج (وإلا سيحذف فواتير الأشهر الجديدة التي تتولّد تلقائياً!)
    migration_flag = c.execute("SELECT SettingValue FROM AppSettings WHERE SettingKey='monthly_fees_migration_done'").fetchone()
    if not migration_flag:
        c.execute("DELETE FROM FeeReceipts")
        c.execute("DELETE FROM Fees")
        c.execute("INSERT OR REPLACE INTO AppSettings (SettingKey, SettingValue) VALUES ('monthly_fees_migration_done', datetime('now','localtime'))")
        print("🔄 تمت ترقية قاعدة البيانات: تم تصفير المبلغ المطلوب (حذف كل الفواتير والإيصالات القديمة) والبدء بنظام الرسوم الشهرية التلقائية")

    # تحديث أسماء الصفوف القديمة (الأول الابتدائي / الأول الإعدادي / الأول الثانوي ...)
    # إلى التسمية الجديدة المرتبة برقم الصف فقط (الأول .. الثاني عشر)
    # GradeID لا يتغير أبداً، فقط الاسم المعروض، لذلك لا تتأثر بيانات الطلاب أو تسجيلهم في الصفوف
    new_grade_names = {
        -3: "مستمع", -2: "بستان", -1: "تمهيدي",
        1: "الأول", 2: "الثاني", 3: "الثالث",
        4: "الرابع", 5: "الخامس", 6: "السادس",
        7: "السابع", 8: "الثامن", 9: "التاسع",
        10: "العاشر", 11: "الحادي عشر", 12: "الثاني عشر",
    }
    current_grades = {row[0]: row[1] for row in c.execute("SELECT GradeID, GradeName FROM Grades").fetchall()}
    for gid, new_name in new_grade_names.items():
        if gid in current_grades and current_grades[gid] != new_name:
            c.execute("UPDATE Grades SET GradeName=? WHERE GradeID=?", (new_name, gid))
    print("🔄 تمت ترقية قاعدة البيانات: تم تحديث أسماء الصفوف إلى ترتيبها (الأول .. الثاني عشر)")




    # مواد افتراضية + ترحيل المواد المكتوبة سابقاً في ملف المعلم
    c.execute("SELECT COUNT(*) FROM Subjects")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT OR IGNORE INTO Subjects (SubjectName) VALUES (?)", [
            ("رياضيات",), ("لغة عربية",), ("لغة إنجليزية",), ("علوم",),
            ("تربية إسلامية",), ("اجتماعيات",), ("حاسوب",)
        ])
    for row in c.execute("SELECT UserID, Subject FROM TeacherProfiles WHERE Subject IS NOT NULL AND TRIM(Subject)!=''").fetchall():
        for name in [x.strip() for x in str(row[1]).replace('،', ',').split(',') if x.strip()]:
            c.execute("INSERT OR IGNORE INTO Subjects (SubjectName) VALUES (?)", (name,))
            sid = c.execute("SELECT SubjectID FROM Subjects WHERE SubjectName=?", (name,)).fetchone()[0]
            c.execute("INSERT OR IGNORE INTO TeacherSubjects (UserID, SubjectID) VALUES (?,?)", (row[0], sid))

    # ===== المواد الأساسية الإلزامية لكل صف =====
    # تُضاف تلقائياً لكل صف (حالي أو جديد يُنشأ لاحقاً) دون حذف أي مواد أخرى مضافة له
    core_subject_names = ["لغة عربية", "لغة إنجليزية", "رياضيات", "اجتماعيات"]
    for _name in core_subject_names:
        c.execute("INSERT OR IGNORE INTO Subjects (SubjectName) VALUES (?)", (_name,))
    core_subject_ids = [
        c.execute("SELECT SubjectID FROM Subjects WHERE SubjectName=?", (_name,)).fetchone()[0]
        for _name in core_subject_names
    ]
    _all_grade_ids = [row[0] for row in c.execute("SELECT GradeID FROM Grades").fetchall()]
    _added_core_link = False
    for _gid in _all_grade_ids:
        for _sid in core_subject_ids:
            c.execute("INSERT OR IGNORE INTO GradeSubjects (GradeID, SubjectID) VALUES (?,?)", (_gid, _sid))
            if c.rowcount:
                _added_core_link = True
    if _added_core_link:
        print("🔄 تمت ترقية قاعدة البيانات: تمت إضافة المواد الأساسية (لغة عربية، لغة إنجليزية، رياضيات، اجتماعيات) لكل صف")

    # ===== اعتماد رسم شهري افتراضي 60 شيكل لكل الطلاب =====
    fee60_flag = c.execute(
        "SELECT SettingValue FROM AppSettings WHERE SettingKey='default_monthly_fee_60_v1'"
    ).fetchone()
    if not fee60_flag:
        c.execute("UPDATE Students SET MonthlyFee=60")
        c.execute(
            "INSERT OR REPLACE INTO AppSettings (SettingKey, SettingValue) "
            "VALUES ('default_monthly_fee_60_v1', datetime('now','localtime'))"
        )
        print("💰 تم اعتماد رسم شهري افتراضي 60 شيكل لجميع الطلاب الحاليين")

    # إنشاء مدير افتراضي لأول تشغيل
    def _hash_password(password):
        salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 600000)
        return f"pbkdf2_sha256$600000${salt}${dk.hex()}"

    c.execute("SELECT COUNT(*) FROM Users")
    if c.fetchone()[0] == 0:
        c.execute("""
            INSERT INTO Users (Username, PasswordHash, FullName, Role, IsActive)
            VALUES (?,?,?,?,1)
        """, ('admin', _hash_password(secrets.token_urlsafe(32)), 'مدير النظام', 'admin'))
        print("✅ تم إنشاء حساب المدير. عيّن كلمة مروره باستخدام tools/set_admin_password.py")

    conn.commit()
    conn.close()
    print("✅ تم تهيئة قاعدة البيانات بنجاح")

    # توليد فواتير أي أشهر مفقودة للرسوم الشهرية (يعمل في كل مرة يُشغَّل فيها البرنامج)
    try:
        generated = generate_missing_monthly_fees()
        if generated:
            print(f"🧾 تم توليد {generated} فاتورة شهرية جديدة (مع ترحيل أي مبلغ متبقٍ من الأشهر السابقة تلقائياً)")
    except Exception as e:
        print(f"⚠️ تعذّر توليد الفواتير الشهرية تلقائياً: {e}")


MONTHLY_FEES_START_YEAR = 2026
MONTHLY_FEES_START_MONTH = 6  # يونيو 2026: الشهر الذي بدأ منه نظام الرسوم الشهرية التلقائي



# ===== STUDENT_FINANCE_LEDGER_PATCH_V1 =====
def ensure_student_finance_schema(conn=None):
    own = conn is None
    if own:
        conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS StudentFinanceAccounts (
            StudentID INTEGER PRIMARY KEY,
            CreditBalance REAL NOT NULL DEFAULT 0,
            UpdatedAt TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS StudentFinanceLedger (
            LedgerID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            EventDate TEXT,
            EventType TEXT NOT NULL,
            Amount REAL NOT NULL DEFAULT 0,
            CreditChange REAL NOT NULL DEFAULT 0,
            CreditBalance REAL NOT NULL DEFAULT 0,
            ReferenceKind TEXT,
            ReferenceID INTEGER,
            Notes TEXT,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        )
    """)
    c.execute("""
        INSERT OR IGNORE INTO StudentFinanceAccounts (StudentID, CreditBalance)
        SELECT StudentID, 0 FROM Students
    """)
    if own:
        conn.commit()
        conn.close()


def _finance_credit_balance_conn(conn, student_id):
    ensure_student_finance_schema(conn)
    row = conn.execute("SELECT CreditBalance FROM StudentFinanceAccounts WHERE StudentID=?", (student_id,)).fetchone()
    return float(row[0] or 0) if row else 0.0


def finance_get_credit(student_id):
    conn = get_connection()
    try:
        return _finance_credit_balance_conn(conn, student_id)
    finally:
        conn.close()


def _finance_append_note(conn, table, id_col, record_id, note):
    if not note:
        return
    conn.execute(
        f"UPDATE {table} SET Notes=CASE WHEN Notes IS NULL OR TRIM(Notes)='' THEN ? ELSE Notes || char(10) || ? END WHERE {id_col}=?",
        (note, note, record_id)
    )


def _finance_log_conn(conn, student_id, event_type, amount=0, credit_change=0,
                      ref_kind=None, ref_id=None, notes='', event_date=None):
    bal = _finance_credit_balance_conn(conn, student_id)
    conn.execute("""
        INSERT INTO StudentFinanceLedger
        (StudentID, EventDate, EventType, Amount, CreditChange, CreditBalance, ReferenceKind, ReferenceID, Notes)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (student_id, event_date or datetime.now().strftime('%Y-%m-%d'), event_type,
          float(amount or 0), float(credit_change or 0), bal, ref_kind, ref_id, notes or ''))
    return bal


def _finance_change_credit_conn(conn, student_id, delta, event_type, amount=0,
                                ref_kind=None, ref_id=None, notes='', event_date=None):
    old = _finance_credit_balance_conn(conn, student_id)
    new = max(0.0, old + float(delta or 0))
    conn.execute("""
        INSERT INTO StudentFinanceAccounts(StudentID, CreditBalance, UpdatedAt)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(StudentID) DO UPDATE SET CreditBalance=excluded.CreditBalance, UpdatedAt=excluded.UpdatedAt
    """, (student_id, new))
    conn.execute("""
        INSERT INTO StudentFinanceLedger
        (StudentID, EventDate, EventType, Amount, CreditChange, CreditBalance, ReferenceKind, ReferenceID, Notes)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (student_id, event_date or datetime.now().strftime('%Y-%m-%d'), event_type,
          float(amount or 0), float(delta or 0), new, ref_kind, ref_id, notes or ''))
    return new


def _finance_use_credit_conn(conn, student_id, fee_kind, fee_id, max_amount=None,
                             event_date=None, context=''):
    table = 'MonthlyFees' if fee_kind == 'monthly' else 'Fees'
    idcol = 'MonthlyFeeID' if fee_kind == 'monthly' else 'FeeID'
    row = conn.execute(f"SELECT * FROM {table} WHERE {idcol}=?", (fee_id,)).fetchone()
    if not row:
        return 0.0
    amount = float(row['Amount'] or 0)
    paid = float(row['AmountPaid'] or 0)
    remaining = max(0.0, amount - paid)
    credit = _finance_credit_balance_conn(conn, student_id)
    use = min(remaining, credit)
    if max_amount is not None:
        use = min(use, max(0.0, float(max_amount)))
    if use <= 0.0001:
        return 0.0
    new_paid = paid + use
    d = event_date or datetime.now().strftime('%Y-%m-%d')
    conn.execute(f"UPDATE {table} SET AmountPaid=?, PaidDate=? WHERE {idcol}=?", (new_paid, d, fee_id))
    suffix = f" لشهر {row['Month']}/{row['Year']}" if fee_kind == 'monthly' else ''
    note = f"[{d}] تم استخدام {use:.2f} ₪ من رصيد الطالب{suffix}"
    if context:
        note += f" — {context}"
    _finance_append_note(conn, table, idcol, fee_id, note)
    _finance_change_credit_conn(conn, student_id, -use, 'استخدام رصيد', amount=use,
                                ref_kind=fee_kind, ref_id=fee_id, notes=note, event_date=d)
    return use


def finance_apply_credit_to_future_monthly(student_id, after_year, after_month):
    total_used = 0.0
    while True:
        conn = get_connection()
        try:
            ensure_student_finance_schema(conn)
            if _finance_credit_balance_conn(conn, student_id) <= 0.0001:
                return total_used
            row = conn.execute("""
                SELECT * FROM MonthlyFees
                WHERE StudentID=? AND (Year>? OR (Year=? AND Month>?))
                  AND COALESCE(AmountPaid,0) < COALESCE(Amount,0)
                ORDER BY Year, Month LIMIT 1
            """, (student_id, after_year, after_year, after_month)).fetchone()
            if not row:
                return total_used
            used = _finance_use_credit_conn(
                conn, student_id, 'monthly', row['MonthlyFeeID'],
                event_date=datetime.now().strftime('%Y-%m-%d'),
                context='خصم تلقائي من الرصيد المرحّل'
            )
            conn.commit()
            y, m = row['Year'], row['Month']
        finally:
            conn.close()
        if used <= 0.0001:
            return total_used
        total_used += used
        regenerate_carry_over_from(student_id, y, m)
        after_year, after_month = y, m


def finance_process_payment(fee_kind, fee_id, cash_amount, paid_date=None,
                            received_by='أمين الصندوق', user_notes='', photo=None,
                            payment_method='كاش', use_credit=True):
    table = 'MonthlyFees' if fee_kind == 'monthly' else 'Fees'
    idcol = 'MonthlyFeeID' if fee_kind == 'monthly' else 'FeeID'
    cash_amount = max(0.0, float(cash_amount or 0))
    paid_date = paid_date or datetime.now().strftime('%Y-%m-%d')
    conn = get_connection()
    try:
        ensure_student_finance_schema(conn)
        fee = conn.execute(f"SELECT * FROM {table} WHERE {idcol}=?", (fee_id,)).fetchone()
        if not fee:
            raise ValueError('الرسم غير موجود')
        sid = fee['StudentID']
        total = float(fee['Amount'] or 0)

        # حماية من أي زيادة قديمة محفوظة داخل AmountPaid.
        old_paid = float(fee['AmountPaid'] or 0)
        if old_paid > total:
            old_excess = old_paid - total
            conn.execute(f"UPDATE {table} SET AmountPaid=? WHERE {idcol}=?", (total, fee_id))
            note = f"[{paid_date}] تم تحويل زيادة قديمة {old_excess:.2f} ₪ إلى رصيد الطالب"
            _finance_change_credit_conn(conn, sid, old_excess, 'تحويل زيادة قديمة إلى رصيد',
                                        amount=old_excess, ref_kind=fee_kind, ref_id=fee_id,
                                        notes=note, event_date=paid_date)
            _finance_append_note(conn, table, idcol, fee_id, note)

        credit_used = 0.0
        if use_credit:
            credit_used = _finance_use_credit_conn(
                conn, sid, fee_kind, fee_id, event_date=paid_date,
                context='استخدام الرصيد قبل الدفعة النقدية'
            )

        current = conn.execute(f"SELECT * FROM {table} WHERE {idcol}=?", (fee_id,)).fetchone()
        current_paid = float(current['AmountPaid'] or 0)
        remaining = max(0.0, float(current['Amount'] or 0) - current_paid)
        cash_applied = min(cash_amount, remaining)
        excess = max(0.0, cash_amount - cash_applied)
        new_paid = current_paid + cash_applied
        conn.execute(f"UPDATE {table} SET AmountPaid=?, PaidDate=? WHERE {idcol}=?", (new_paid, paid_date, fee_id))

        parts = []
        if cash_amount > 0:
            parts.append(f"دفعة نقدية {cash_amount:.2f} ₪")
        if credit_used > 0:
            parts.append(f"استخدام {credit_used:.2f} ₪ من رصيد الطالب")
        if cash_applied > 0:
            parts.append(f"سداد {cash_applied:.2f} ₪ على الرسم")
        if excess > 0:
            parts.append(f"إضافة {excess:.2f} ₪ رصيداً لصالح الطالب")
        if not parts:
            parts.append('لم يحدث تغيير مالي')
        auto_note = f"[{paid_date}] " + '؛ '.join(parts)
        if user_notes and str(user_notes).strip():
            auto_note += f" — ملاحظة المستخدم: {str(user_notes).strip()}"

        if excess > 0:
            new_credit = _finance_change_credit_conn(
                conn, sid, excess, 'رصيد من دفعة زائدة', amount=cash_amount,
                ref_kind=fee_kind, ref_id=fee_id, notes=auto_note, event_date=paid_date
            )
            auto_note += f". الرصيد الحالي: {new_credit:.2f} ₪"
        elif cash_amount > 0:
            _finance_log_conn(conn, sid, 'دفعة', amount=cash_amount,
                              ref_kind=fee_kind, ref_id=fee_id,
                              notes=auto_note, event_date=paid_date)

        _finance_append_note(conn, table, idcol, fee_id, auto_note)

        rid = None
        if cash_amount > 0:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(FeeReceipts)").fetchall()}
            if 'PaymentMethod' in cols:
                cur = conn.execute("""
                    INSERT INTO FeeReceipts
                    (FeeID, FeeKind, StudentID, Amount, PaidDate, ReceivedBy, Notes, Photo, PaymentMethod)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (fee_id, fee_kind, sid, cash_amount, paid_date, received_by,
                      auto_note, photo, payment_method or 'كاش'))
            else:
                cur = conn.execute("""
                    INSERT INTO FeeReceipts
                    (FeeID, FeeKind, StudentID, Amount, PaidDate, ReceivedBy, Notes, Photo)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (fee_id, fee_kind, sid, cash_amount, paid_date, received_by,
                      auto_note, photo))
            rid = cur.lastrowid
        conn.commit()
        result = {
            'ReceiptID': rid,
            'StudentID': sid,
            'CashAmount': cash_amount,
            'CashApplied': cash_applied,
            'CreditUsed': credit_used,
            'ExcessToCredit': excess,
            'Message': auto_note,
            'CreditBalance': _finance_credit_balance_conn(conn, sid),
            'Year': current['Year'] if fee_kind == 'monthly' else None,
            'Month': current['Month'] if fee_kind == 'monthly' else None,
        }
    finally:
        conn.close()

    if fee_kind == 'monthly':
        regenerate_carry_over_from(result['StudentID'], result['Year'], result['Month'])
        finance_apply_credit_to_future_monthly(result['StudentID'], result['Year'], result['Month'])
    return result


def finance_account_snapshot(student_id):
    conn = get_connection()
    try:
        ensure_student_finance_schema(conn)
        credit = _finance_credit_balance_conn(conn, student_id)
        latest = conn.execute("""
            SELECT MonthlyFeeID, Year, Month, Amount, AmountPaid
            FROM MonthlyFees WHERE StudentID=?
            ORDER BY Year DESC, Month DESC LIMIT 1
        """, (student_id,)).fetchone()
        monthly_due = max(0.0, float(latest['Amount'] or 0) - float(latest['AmountPaid'] or 0)) if latest else 0.0
        extra_due = float(conn.execute(
            "SELECT COALESCE(SUM(MAX(COALESCE(Amount,0)-COALESCE(AmountPaid,0),0)),0) FROM Fees WHERE StudentID=?",
            (student_id,)
        ).fetchone()[0] or 0)
        total_due = monthly_due + extra_due
        cash_total = float(conn.execute(
            "SELECT COALESCE(SUM(Amount),0) FROM FeeReceipts WHERE StudentID=?", (student_id,)
        ).fetchone()[0] or 0)
        monthly_cash = float(conn.execute(
            "SELECT COALESCE(SUM(Amount),0) FROM FeeReceipts WHERE StudentID=? AND FeeKind='monthly'", (student_id,)
        ).fetchone()[0] or 0)
        extra_cash = float(conn.execute(
            "SELECT COALESCE(SUM(Amount),0) FROM FeeReceipts WHERE StudentID=? AND FeeKind='extra'", (student_id,)
        ).fetchone()[0] or 0)
        return {
            'CreditBalance': credit,
            'MonthlyDue': monthly_due,
            'ExtraDue': extra_due,
            'TotalDueBeforeCredit': total_due,
            'NetDue': max(0.0, total_due - credit),
            'NetCredit': max(0.0, credit - total_due),
            'CashReceivedTotal': cash_total,
            'MonthlyCashReceived': monthly_cash,
            'ExtraCashReceived': extra_cash,
        }
    finally:
        conn.close()


def finance_ledger_rows(student_id, limit=100):
    conn = get_connection()
    try:
        ensure_student_finance_schema(conn)
        rows = conn.execute("""
            SELECT * FROM StudentFinanceLedger
            WHERE StudentID=? ORDER BY LedgerID DESC LIMIT ?
        """, (student_id, int(limit or 100))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
# ===== /STUDENT_FINANCE_LEDGER_PATCH_V1 =====

def generate_missing_monthly_fees():
    """توليد الرسوم الشهرية مع ترحيل النقص واستخدام الرصيد الدائن تلقائياً."""
    conn = get_connection()
    c = conn.cursor()
    ensure_student_finance_schema(conn)
    now = datetime.now()
    cur_year, cur_month = now.year, now.month
    students = c.execute(
        "SELECT StudentID, COALESCE(MonthlyFee,0) as MonthlyFee FROM Students WHERE COALESCE(IsArchived,0)=0"
    ).fetchall()
    total_generated = 0
    for student in students:
        sid = student['StudentID']
        monthly_fee = float(student['MonthlyFee'] or 0)
        last_row = c.execute(
            "SELECT Year,Month,Amount,AmountPaid FROM MonthlyFees WHERE StudentID=? ORDER BY Year DESC,Month DESC LIMIT 1",
            (sid,)
        ).fetchone()
        if last_row:
            year, month = last_row['Year'], last_row['Month']
            carry_over = max(0.0, float(last_row['Amount'] or 0) - float(last_row['AmountPaid'] or 0))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        else:
            year, month = cur_year, cur_month
            carry_over = 0.0
        while (year < cur_year) or (year == cur_year and month <= cur_month):
            amount_due = monthly_fee + carry_over
            c.execute("""
                INSERT OR IGNORE INTO MonthlyFees
                (StudentID,Year,Month,Amount,AmountPaid,CreatedDate)
                VALUES (?,?,?,?,0,date('now'))
            """, (sid, year, month, amount_due))
            if c.rowcount:
                total_generated += 1
                mfid = c.lastrowid
                used = _finance_use_credit_conn(
                    conn, sid, 'monthly', mfid,
                    event_date=now.strftime('%Y-%m-%d'),
                    context='خصم تلقائي عند إنشاء فاتورة الشهر'
                )
                carry_over = max(0.0, amount_due - used)
            else:
                r = c.execute(
                    "SELECT Amount,AmountPaid FROM MonthlyFees WHERE StudentID=? AND Year=? AND Month=?",
                    (sid, year, month)
                ).fetchone()
                carry_over = max(0.0, float(r['Amount'] or 0) - float(r['AmountPaid'] or 0)) if r else amount_due
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    conn.commit()
    conn.close()
    return total_generated



def regenerate_carry_over_from(student_id, year, month):
    """إعادة حساب ترحيل النقص للأشهر اللاحقة مع احترام الرصيد الدائن."""
    conn = get_connection()
    c = conn.cursor()
    ensure_student_finance_schema(conn)
    rows = c.execute("""
        SELECT MonthlyFeeID,Year,Month,Amount,AmountPaid FROM MonthlyFees
        WHERE StudentID=? AND (Year>? OR (Year=? AND Month>=?))
        ORDER BY Year,Month
    """, (student_id, year, year, month)).fetchall()
    student = c.execute(
        "SELECT COALESCE(MonthlyFee,0) as MonthlyFee FROM Students WHERE StudentID=?", (student_id,)
    ).fetchone()
    monthly_fee = float(student['MonthlyFee'] or 0) if student else 0.0
    carry_over = 0.0
    for i, row in enumerate(rows):
        paid = float(row['AmountPaid'] or 0)
        if i == 0:
            amount0 = float(row['Amount'] or 0)
            if paid > amount0:
                excess = paid - amount0
                paid = amount0
                c.execute("UPDATE MonthlyFees SET AmountPaid=? WHERE MonthlyFeeID=?", (paid, row['MonthlyFeeID']))
                note = f"[{datetime.now().strftime('%Y-%m-%d')}] تم تحويل زيادة {excess:.2f} ₪ إلى رصيد الطالب بعد إعادة الحساب"
                _finance_change_credit_conn(conn, student_id, excess, 'رصيد من إعادة حساب', amount=excess,
                                            ref_kind='monthly', ref_id=row['MonthlyFeeID'], notes=note)
                _finance_append_note(conn, 'MonthlyFees', 'MonthlyFeeID', row['MonthlyFeeID'], note)
            carry_over = max(0.0, amount0 - paid)
            continue
        new_amount = monthly_fee + carry_over
        if paid > new_amount:
            excess = paid - new_amount
            paid = new_amount
            note = f"[{datetime.now().strftime('%Y-%m-%d')}] تم تحويل زيادة {excess:.2f} ₪ إلى رصيد الطالب بعد تعديل الترحيل"
            _finance_change_credit_conn(conn, student_id, excess, 'رصيد من تعديل ترحيل', amount=excess,
                                        ref_kind='monthly', ref_id=row['MonthlyFeeID'], notes=note)
            _finance_append_note(conn, 'MonthlyFees', 'MonthlyFeeID', row['MonthlyFeeID'], note)
        c.execute("UPDATE MonthlyFees SET Amount=?,AmountPaid=? WHERE MonthlyFeeID=?",
                  (new_amount, paid, row['MonthlyFeeID']))
        used = _finance_use_credit_conn(
            conn, student_id, 'monthly', row['MonthlyFeeID'],
            event_date=datetime.now().strftime('%Y-%m-%d'),
            context='خصم تلقائي بعد إعادة حساب الترحيل'
        )
        carry_over = max(0.0, new_amount - paid - used)
    conn.commit()
    conn.close()



def ensure_current_monthly_fee(student_id):
    """يضمن فاتورة الشهر الحالي دون رسوم رجعية، مع حمل أي دين سابق فقط واستخدام الرصيد."""
    conn = get_connection()
    c = conn.cursor()
    ensure_student_finance_schema(conn)
    now = datetime.now()
    student = c.execute(
        "SELECT COALESCE(MonthlyFee,60) AS MonthlyFee, COALESCE(IsArchived,0) AS IsArchived FROM Students WHERE StudentID=?",
        (student_id,)
    ).fetchone()
    if not student or student['IsArchived']:
        conn.close()
        return None

    monthly_fee = float(student['MonthlyFee'] if student['MonthlyFee'] is not None else 60)
    existing = c.execute(
        "SELECT MonthlyFeeID FROM MonthlyFees WHERE StudentID=? AND Year=? AND Month=?",
        (student_id, now.year, now.month)
    ).fetchone()
    if existing:
        conn.commit()
        conn.close()
        return existing['MonthlyFeeID']

    # عند استعادة طالب من الأرشيف: لا نفرض رسوم أشهر الأرشفة، لكن لا نسقط ديناً سابقاً.
    prev = c.execute("""
        SELECT Amount,AmountPaid FROM MonthlyFees
        WHERE StudentID=? AND (Year<? OR (Year=? AND Month<?))
        ORDER BY Year DESC,Month DESC LIMIT 1
    """, (student_id, now.year, now.year, now.month)).fetchone()
    carry = max(0.0, float(prev['Amount'] or 0) - float(prev['AmountPaid'] or 0)) if prev else 0.0
    amount = monthly_fee + carry
    c.execute("""
        INSERT INTO MonthlyFees
        (StudentID,Year,Month,Amount,AmountPaid,CreatedDate)
        VALUES (?,?,?,?,0,date('now'))
    """, (student_id, now.year, now.month, amount))
    mfid = c.lastrowid
    if carry > 0.0001:
        note = f"[{now.strftime('%Y-%m-%d')}] تم ترحيل {carry:.2f} ₪ ديناً سابقاً إلى فاتورة الشهر الحالي دون احتساب رسوم فترة الأرشفة"
        _finance_append_note(conn, 'MonthlyFees', 'MonthlyFeeID', mfid, note)
        _finance_log_conn(conn, student_id, 'ترحيل دين سابق', amount=carry,
                          ref_kind='monthly', ref_id=mfid, notes=note,
                          event_date=now.strftime('%Y-%m-%d'))
    _finance_use_credit_conn(
        conn, student_id, 'monthly', mfid,
        event_date=now.strftime('%Y-%m-%d'),
        context='خصم تلقائي عند إنشاء فاتورة الشهر الحالي'
    )
    conn.commit()
    conn.close()
    return mfid



def generate_student_no():
    """توليد رقم طالب تسلسلي (وليس عشوائياً): يبدأ من 0001 ثم يزيد رقماً واحداً
    في كل مرة، مع الاستمرار تلقائياً بعد آخر رقم مستخدم فعلياً في قاعدة البيانات
    لضمان عدم وجود أي تكرار. الرقم بصيغة 4 خانات دائماً (0001 ... 9999)."""
    conn = get_connection()
    rows = conn.execute("SELECT StudentNo FROM Students").fetchall()
    conn.close()
    max_no = 0  # نقطة البداية: أول رقم سيكون 0001
    for row in rows:
        try:
            n = int(str(row[0]).strip())
        except (TypeError, ValueError):
            continue
        if n > max_no:
            max_no = n
    next_no = max_no + 1
    if next_no > 9999:
        # تجاوزنا الحد الأقصى المسموح بأربع خانات - نُعيد كنص بدون اقتطاع لتفادي تكرار الأرقام
        return str(next_no)
    return f"{next_no:04d}"


def renumber_students_sequentially():
    """يُعيد ترقيم جميع الطلاب الحاليين بأرقام تسلسلية بصيغة 4 خانات (0001 -> 9999)
    حسب ترتيب التسجيل (تاريخ التسجيل ثم رقم الطالب الداخلي)، دون التأثير على أي
    بيانات أو علاقات أخرى (الرسوم، أولياء الأمور، الحالة الاجتماعية... إلخ) لأنها
    جميعها مرتبطة بـ StudentID الداخلي وليس برقم الطالب الظاهري StudentNo."""
    conn = get_connection()
    c = conn.cursor()
    rows = c.execute("""
        SELECT StudentID FROM Students
        ORDER BY RegDate IS NULL, RegDate, StudentID
    """).fetchall()

    if len(rows) > 9999:
        conn.close()
        raise ValueError("عدد الطلاب أكبر من 9999، لا يمكن استخدام 4 خانات فقط لكل الطلاب")

    # خطوة وسيطة: نعطي كل الطلاب أرقاماً مؤقتة فريدة لتفادي تعارض UNIQUE أثناء الترقيم
    for i, row in enumerate(rows, start=1):
        c.execute("UPDATE Students SET StudentNo=? WHERE StudentID=?", (f"TMP{i}", row["StudentID"]))
    conn.commit()

    # الترقيم النهائي التسلسلي بصيغة 4 خانات
    for i, row in enumerate(rows, start=1):
        c.execute("UPDATE Students SET StudentNo=? WHERE StudentID=?", (f"{i:04d}", row["StudentID"]))



    conn.commit()
    conn.close()
    return len(rows)


if __name__ == "__main__":
    init_database()

# ARCHIVE_REASON_NOTE_PATCH_V1
