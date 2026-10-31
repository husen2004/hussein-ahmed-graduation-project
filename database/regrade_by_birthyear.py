"""
سكربت إعادة ترتيب الطلاب على الصفوف الدراسية حسب سنة الميلاد
القاعدة: الطالب المولود في 2020 -> الصف الأول، 2019 -> الثاني، 2018 -> الثالث ... إلخ
أي: رقم الصف = 2021 - سنة الميلاد (بحد أدنى 1 وحد أعلى 12)
- الطلاب الذين لا يوجد لديهم تاريخ ميلاد مسجل: لا يتم تغيير صفهم (يبقى كما هو لمراجعة يدوية)
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "students.db")

GRADE_NAMES = {
    1: "الأول", 2: "الثاني", 3: "الثالث",
    4: "الرابع", 5: "الخامس", 6: "السادس",
    7: "السابع", 8: "الثامن", 9: "التاسع",
    10: "العاشر", 11: "الحادي عشر", 12: "الثاني عشر",
}


def grade_from_birthyear(year):
    g = 2021 - year
    if g < 1 or g > 12:
        return None  # خارج النطاق المعروف - يحتاج مراجعة يدوية
    return g


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    students = conn.execute("""
        SELECT s.StudentID, s.StudentNo, s.FullName, s.BirthDate, e.GradeID as OldGradeID
        FROM Students s
        LEFT JOIN Enrollments e ON s.StudentID = e.StudentID
        ORDER BY s.StudentID
    """).fetchall()

    updated = []
    skipped_no_birthdate = []
    skipped_out_of_range = []
    unchanged_same_grade = []

    for s in students:
        bd = s["BirthDate"]
        if not bd:
            skipped_no_birthdate.append(s)
            continue
        try:
            year = int(str(bd)[:4])
        except ValueError:
            skipped_no_birthdate.append(s)
            continue

        new_grade = grade_from_birthyear(year)
        if new_grade is None:
            skipped_out_of_range.append((s, year))
            continue

        if new_grade == s["OldGradeID"]:
            unchanged_same_grade.append((s, new_grade))
            continue

        conn.execute(
            "UPDATE Enrollments SET GradeID=? WHERE StudentID=?",
            (new_grade, s["StudentID"])
        )
        updated.append((s, year, new_grade))

    conn.commit()

    print(f"✅ تم تحديث صف {len(updated)} طالب")
    print(f"➖ بقي صفهم كما هو (مطابق أصلاً للمولد): {len(unchanged_same_grade)}")
    print(f"⚠️ بلا تاريخ ميلاد (لم يتم تغيير صفهم): {len(skipped_no_birthdate)}")
    print(f"⚠️ سنة ميلاد خارج نطاق الصفوف 1-12 (لم يتم تغيير صفهم): {len(skipped_out_of_range)}")
    print()

    if skipped_no_birthdate:
        print("=== الطلاب بلا تاريخ ميلاد (يحتاجون تحديد الصف يدوياً) ===")
        for s in skipped_no_birthdate:
            old_name = GRADE_NAMES.get(s["OldGradeID"], "-")
            print(f"  #{s['StudentNo']}  {s['FullName']}  (الصف الحالي: {old_name})")
        print()

    if skipped_out_of_range:
        print("=== الطلاب بسنة ميلاد خارج النطاق المعروف (1-12) ===")
        for s, year in skipped_out_of_range:
            print(f"  #{s['StudentNo']}  {s['FullName']}  (سنة الميلاد: {year})")
        print()

    print("=== توزيع الطلاب على الصفوف بعد التحديث ===")
    dist = conn.execute("""
        SELECT gr.GradeID, gr.GradeName, COUNT(e.StudentID) as Cnt
        FROM Grades gr LEFT JOIN Enrollments e ON gr.GradeID = e.GradeID
        GROUP BY gr.GradeID ORDER BY gr.GradeID
    """).fetchall()
    for row in dist:
        print(f"  {row['GradeName']}: {row['Cnt']}")

    conn.close()


if __name__ == "__main__":
    main()
