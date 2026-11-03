# مشروع تخرج حسين أحمد

**مشروع مدرسة حسين أحمد للنشر**

إعداد حسين طارق حسين أحمد — الرقم الجامعي 1320222455.
المشرف: د. عبد الباسط عبد الله المصري.

تم رفع الكود والتقرير والمخططات والعرض إلى هذا المستودع الخاص بتاريخ 3 نوفمبر 2026.

## الملفات

- [تقرير Word](docs/Hussein_Ahmed_Graduation_Report.docx)
- [تقرير PDF](docs/Hussein_Ahmed_Graduation_Report.pdf)
- [عرض المناقشة العربي](docs/Hussein_Ahmed_Defense_Arabic.pptx)
- [المراجع لاستيرادها في مندلي](docs/References_Mendeley.ris)
- المخططات القابلة للتحرير في diagrams، ونتائج الاختبارات في evidence.
- [حالة التسليم الأكاديمي والخطوات المتبقية](DELIVERY_README_AR.md)
- [دليل PythonAnywhere](DEPLOY_PYTHONANYWHERE_AR.md)

## التشغيل

يتطلب Python 3.10 أو أحدث. قاعدة البيانات المرفقة للعرض وببيانات وهمية. لا توجد كلمة مرور افتراضية ثابتة؛ عيّن كلمة مرور خاصة قبل الدخول:

```bash
python database/init_db.py
python tools/set_admin_password.py --username admin
python tools/set_admin_password.py --username teacher
python server.py
```

ثم افتح http://127.0.0.1:8000 . أمر المعلم خاص بحساب العرض المرفق؛ عند إنشاء قاعدة فارغة جديدة يُنشأ المدير فقط.

```bash
python -m unittest discover -s tests -v
```

نجحت اختبارات التكامل الأربعة بعد إزالة كلمات المرور الافتراضية. إعداد PythonAnywhere موجود؛ رفع هذا المستودع لا يحدّث الموقع السحابي تلقائيًا، ولا توجد مزامنة تلقائية بين قاعدتي البيانات المحلية والسحابية.
