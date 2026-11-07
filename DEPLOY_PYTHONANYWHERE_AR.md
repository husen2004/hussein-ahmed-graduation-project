> **للموقع الموجود على حساب hussin2004:** لا تستخدم خطوات التثبيت الجديد ولا تستبدل قاعدة البيانات. ابدأ بدليل [تحديث الحساب الحالي](PYTHONANYWHERE_EXISTING_ACCOUNT_AR.md) وأداة [التحديث مع النسخ الاحتياطي](tools/deploy_existing_pythonanywhere.py). آخر فحص خارجي في 7 نوفمبر 2026 لم يتحقق من تشغيل الموقع أو نشر آخر إصدار.

# نشر مشروع تخرج حسين أحمد على PythonAnywhere

بهذه الإعدادات يعمل المشروع بطريقتين من نفس الملفات:

- محليًا عبر `START_OFFLINE.bat` على `127.0.0.1:8000`.
- سحابيًا عبر WSGI على نطاق PythonAnywhere مع HTTPS الذي توفره المنصة.

## قبل الرفع

1. شغّل الاختبارات محليًا: `python -m unittest discover -s tests -v`.
2. قاعدة البيانات داخل حزمة التسليم وهمية. إذا أردت نقل بيانات تشغيل فعلية، أنشئ نسخة احتياطية متسقة من النظام أولًا.
3. لا ترفع أي قاعدة بيانات قديمة أو ملفات خاصة لا تريد نشرها.

## 1. رفع الملفات

1. سجّل الدخول إلى PythonAnywhere.
2. من **Files** ارفع ملف ZIP الخاص بالمشروع.
3. افتح **Bash Console** ثم نفّذ، مع تعديل اسم الملف عند الحاجة:

```bash
mkdir -p ~/afaq-school-system
unzip AFAQ_School_Management_Graduation_Project.zip -d ~/afaq-school-system
cd ~/afaq-school-system
python3 database/init_db.py
```

إذا نتج عن فك الضغط مجلد إضافي اسمه `project`، اجعل `PROJECT_HOME` في الخطوات التالية يشير إليه.

## 2. تغيير كلمة مرور المدير قبل فتح الموقع

```bash
cd ~/afaq-school-system
python3 tools/set_admin_password.py --username admin
```

استخدم كلمة مرور قوية لا تقل عن 10 محارف. الأداة تلغي الجلسات القديمة تلقائيًا.

## 3. إنشاء Web App

1. من تبويب **Web** اختر **Add a new web app**.
2. اختر نطاق حسابك ثم **Manual configuration**.
3. اختر إصدار Python المتاح والمتوافق مع Python 3.
4. افتح رابط ملف WSGI من تبويب Web.
5. استبدل محتواه بالإعداد التالي بعد وضع اسم مستخدمك بدل `YOUR_USERNAME`:

```python
import os
import sys

PROJECT_HOME = "/home/YOUR_USERNAME/afaq-school-system"
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

os.environ["AFAQ_CLOUD"] = "1"
os.environ["AFAQ_SQLITE_WAL"] = "1"
os.environ["AFAQ_DB_PATH"] = os.path.join(PROJECT_HOME, "database", "students.db")
os.environ["AFAQ_BACKUP_DIR"] = os.path.join(PROJECT_HOME, "database", "backups")

from wsgi import application
```

يوجد المثال نفسه في `pythonanywhere_wsgi.example.py`.

## 4. تثبيت دعم Excel الاختياري

من Bash Console:

```bash
python3 -m pip install --user -r ~/afaq-school-system/requirements.txt
```

النظام الأساسي يعمل من دون هذه الحزمة، لكنها مطلوبة لعمليات Excel.

## 5. إعادة التحميل والتحقق

1. ارجع إلى تبويب **Web** واضغط **Reload**.
2. افتح: `https://YOUR_USERNAME.pythonanywhere.com/`.
3. تحقق من صفحة الصحة: `https://YOUR_USERNAME.pythonanywhere.com/api/health`.
4. سجّل الدخول بكلمة المرور الجديدة.
5. أنشئ نسخة احتياطية تجريبية وتأكد من إمكانية تنزيلها.

## 6. نقل قاعدة بيانات موجودة بأمان

1. أوقف الكتابة على النسخة المحلية مؤقتًا.
2. أنشئ نسخة من داخل شاشة النسخ الاحتياطي أو باستخدام SQLite backup API.
3. في PythonAnywhere أوقف/عطّل الموقع مؤقتًا أو تجنب دخول المستخدمين.
4. احتفظ بنسخة من `~/afaq-school-system/database/students.db` الحالية.
5. ارفع النسخة الجديدة بالاسم نفسه ثم اضغط **Reload**.
6. افحص `/api/health` وسجّل الدخول وراجع أعداد الطلبة والرسوم.

## إعدادات الحماية المضافة

- قاعدة البيانات ومسار النسخ الاحتياطية يُحددان بمتغيرات بيئة مطلقة.
- SQLite يعمل بمهلة انتظار 30 ثانية، ويستخدم WAL في النمط السحابي.
- يضيف النمط السحابي ترويسة HSTS إلى ترويسات الحماية الحالية.
- أخطاء WSGI تُسجل في سجل الأخطاء ولا تُظهر تفاصيل الاستثناء للزائر.
- الإرسال التلقائي للرسائل يبقى معطلًا في النسخة السحابية أيضًا.

## حدود مهمة

- SQLite مناسب كبداية لحساب واحد وحمل منخفض. عند توسع الاستخدام المتزامن، انقل قاعدة البيانات إلى PostgreSQL بعد إعادة تصميم طبقة البيانات واختبارها.
- لا تشارك كلمة مرور PythonAnywhere أو كلمة مرور المدير في ملف أو محادثة عامة.
- احتفظ بنسخة احتياطية خارج PythonAnywhere بصورة دورية.
- بعد أي تعديل في الملفات أو WSGI، اضغط **Reload** من تبويب Web.

دليل PythonAnywhere الرسمي:

- https://help.pythonanywhere.com/pages/WebAppBasics/
- https://help.pythonanywhere.com/pages/UploadingAndDownloadingFiles/
