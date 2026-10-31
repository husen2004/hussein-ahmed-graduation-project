CREATE INDEX idx_activity_user_date ON ActivityLogs(UserID, CreatedAt DESC);

CREATE INDEX idx_cash_debts_student ON CashDebts(StudentID, DebtDate DESC);

CREATE INDEX idx_memberships_grade ON StudentClassMemberships(GradeID, Section);

CREATE UNIQUE INDEX idx_teacherprofiles_serial_unique
        ON TeacherProfiles(SerialNo)
        WHERE SerialNo IS NOT NULL AND TRIM(SerialNo)!=''
    ;

CREATE TABLE ActivityLogs (
            ActivityID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER,
            Username TEXT,
            FullName TEXT,
            Action TEXT NOT NULL,
            Method TEXT,
            Path TEXT,
            Details TEXT,
            CreatedAt TEXT DEFAULT (datetime('now','localtime'))
        );

CREATE TABLE Aids (
            AidID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            AidType TEXT,
            Amount REAL,
            AidDate TEXT,
            GivenBy TEXT,
            Notes TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        );

CREATE TABLE AppSettings (
            SettingKey TEXT PRIMARY KEY,
            SettingValue TEXT
        );

CREATE TABLE Attendance (
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
        );

CREATE TABLE CashDebtPayments (
            PaymentID INTEGER PRIMARY KEY AUTOINCREMENT,
            DebtID INTEGER NOT NULL,
            Amount REAL NOT NULL CHECK(Amount > 0),
            PaymentDate TEXT NOT NULL DEFAULT (date('now')),
            ReceivedBy TEXT,
            Notes TEXT,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (DebtID) REFERENCES CashDebts(DebtID) ON DELETE CASCADE
        );

CREATE TABLE CashDebts (
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
        );

CREATE TABLE CommunicationCampaigns (
            CampaignID INTEGER PRIMARY KEY AUTOINCREMENT,
            Title TEXT NOT NULL,
            MessageBody TEXT NOT NULL,
            Status TEXT NOT NULL DEFAULT 'confirmed_manual',
            RecipientCount INTEGER NOT NULL DEFAULT 0,
            CreatedBy INTEGER,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            ConfirmedAt TEXT,
            FOREIGN KEY (CreatedBy) REFERENCES Users(UserID)
        );

CREATE TABLE CommunicationRecipients (
            RecipientID INTEGER PRIMARY KEY AUTOINCREMENT,
            CampaignID INTEGER NOT NULL,
            StudentID INTEGER NOT NULL,
            Mobile TEXT NOT NULL,
            DeliveryStatus TEXT NOT NULL DEFAULT 'جاهز للإرسال اليدوي',
            FOREIGN KEY (CampaignID) REFERENCES CommunicationCampaigns(CampaignID) ON DELETE CASCADE,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            UNIQUE(CampaignID, StudentID)
        );

CREATE TABLE Enrollments (
            EnrollID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL,
            SchoolID INTEGER,
            GradeID INTEGER,
            Section TEXT,
            AcademicYear TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            FOREIGN KEY (SchoolID) REFERENCES Schools(SchoolID),
            FOREIGN KEY (GradeID) REFERENCES Grades(GradeID)
        );

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
        );

CREATE TABLE Fees (
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
        );

CREATE TABLE FinancialCategories (
            FinCategoryID INTEGER PRIMARY KEY AUTOINCREMENT,
            CategoryName TEXT NOT NULL
        );

CREATE TABLE FinancialStatus (
            FinancialStatusID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL UNIQUE,
            FinCategoryID INTEGER,
            Notes TEXT,
            UpdatedDate TEXT DEFAULT (date('now')),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE,
            FOREIGN KEY (FinCategoryID) REFERENCES FinancialCategories(FinCategoryID)
        );

CREATE TABLE Gender (
            GenderID INTEGER PRIMARY KEY,
            GenderName TEXT NOT NULL
        );

CREATE TABLE Governorates (
            GovID INTEGER PRIMARY KEY AUTOINCREMENT,
            GovName TEXT NOT NULL
        );

CREATE TABLE GradeSubjects (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            GradeID INTEGER NOT NULL,
            SubjectID INTEGER NOT NULL,
            FOREIGN KEY (GradeID) REFERENCES Grades(GradeID) ON DELETE CASCADE,
            FOREIGN KEY (SubjectID) REFERENCES Subjects(SubjectID) ON DELETE CASCADE,
            UNIQUE(GradeID, SubjectID)
        );

CREATE TABLE Grades (
            GradeID INTEGER PRIMARY KEY,
            GradeName TEXT NOT NULL
        );

CREATE TABLE GraduationProjects (
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
        );

CREATE TABLE HealthStatus (
            HealthStatusID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL UNIQUE,
            HealthType TEXT,
            Notes TEXT,
            UpdatedDate TEXT DEFAULT (date('now')),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        );

CREATE TABLE MonthlyFees (
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
        );

CREATE TABLE Orphans (
            OrphanID INTEGER PRIMARY KEY AUTOINCREMENT,
            StudentID INTEGER NOT NULL UNIQUE,
            OrphanType TEXT,
            OrphanDate TEXT,
            SupportedBy TEXT,
            Notes TEXT,
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        );

CREATE TABLE ParentStudents (
        ParentID INTEGER NOT NULL,
        StudentID INTEGER NOT NULL UNIQUE,
        PRIMARY KEY (ParentID, StudentID),
        FOREIGN KEY (ParentID) REFERENCES Parents(ParentID) ON DELETE CASCADE,
        FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
    );

CREATE TABLE Parents (
            ParentID INTEGER PRIMARY KEY AUTOINCREMENT,
            ParentName TEXT NOT NULL,
            Relation TEXT,
            Mobile TEXT,
            Job TEXT,
            NationalID TEXT
        );

CREATE TABLE Schools (
            SchoolID INTEGER PRIMARY KEY AUTOINCREMENT,
            SchoolName TEXT NOT NULL,
            GovID INTEGER,
            Region TEXT,
            Phone TEXT,
            FOREIGN KEY (GovID) REFERENCES Governorates(GovID)
        );

CREATE TABLE Status (
            StatusID INTEGER PRIMARY KEY,
            StatusName TEXT NOT NULL
        );

CREATE TABLE StudentClassMemberships (
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
        );

CREATE TABLE StudentFinanceAccounts (
            StudentID INTEGER PRIMARY KEY,
            CreditBalance REAL NOT NULL DEFAULT 0,
            UpdatedAt TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (StudentID) REFERENCES Students(StudentID) ON DELETE CASCADE
        );

CREATE TABLE StudentFinanceLedger (
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
        );

CREATE TABLE StudentMarks (
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
        );

CREATE TABLE Students (
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
            MonthlyFee REAL DEFAULT 60, IsArchived INTEGER NOT NULL DEFAULT 0, ArchivedDate TEXT, ArchiveReason TEXT, ArchiveNote TEXT,
            FOREIGN KEY (GenderID) REFERENCES Gender(GenderID),
            FOREIGN KEY (StatusID) REFERENCES Status(StatusID),
            FOREIGN KEY (GovID) REFERENCES Governorates(GovID)
        );

CREATE TABLE Subjects (
            SubjectID INTEGER PRIMARY KEY AUTOINCREMENT,
            SubjectName TEXT NOT NULL UNIQUE,
            Notes TEXT
        );

CREATE TABLE TeacherGrades (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            GradeID INTEGER NOT NULL,
            Section TEXT,
            FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE,
            FOREIGN KEY (GradeID) REFERENCES Grades(GradeID),
            UNIQUE(UserID, GradeID, Section)
        );

CREATE TABLE TeacherProfiles (
            TeacherID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL UNIQUE,
            NationalID TEXT,
            Phone TEXT,
            SerialNo TEXT,
            Subject TEXT,
            Notes TEXT,
            FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );

CREATE TABLE TeacherSubjects (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            UserID INTEGER NOT NULL,
            SubjectID INTEGER NOT NULL,
            FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE,
            FOREIGN KEY (SubjectID) REFERENCES Subjects(SubjectID) ON DELETE CASCADE,
            UNIQUE(UserID, SubjectID)
        );

CREATE TABLE UserSessions (
            Token TEXT PRIMARY KEY,
            UserID INTEGER NOT NULL,
            CreatedAt TEXT DEFAULT (datetime('now','localtime')),
            ExpiresAt TEXT,
            FOREIGN KEY (UserID) REFERENCES Users(UserID) ON DELETE CASCADE
        );

CREATE TABLE Users (
            UserID INTEGER PRIMARY KEY AUTOINCREMENT,
            Username TEXT NOT NULL UNIQUE,
            PasswordHash TEXT NOT NULL,
            FullName TEXT NOT NULL,
            Role TEXT NOT NULL DEFAULT 'teacher',
            IsActive INTEGER DEFAULT 1,
            CanEnterMarks INTEGER NOT NULL DEFAULT 1,
            LastLogin TEXT,
            CreatedAt TEXT DEFAULT (datetime('now','localtime'))
        );