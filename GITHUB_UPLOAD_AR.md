# رفع المشروع على GitHub

الملفات مجهزة، ولا يوجد رابط مستودع منشور لهذه الحزمة حتى الآن.

1. سجل الدخول إلى حسابك husen2004.
2. أنشئ مستودعًا جديدًا باسم hussein-ahmed-graduation-project.
3. اختر الرؤية المناسبة لتعليمات الجامعة. يمكن البدء بمستودع خاص وإعطاء المشرف صلاحية الوصول.
4. ارفع محتويات مجلد المشروع بعد فك الضغط، بما فيها docs وdiagrams وevidence والكود؛ لا ترفع ملف ZIP وحده بدل الملفات القابلة للتصفح.
5. افتح README وتحقق من ظهور التقرير والعرض والكود وقاعدة العرض.
6. انسخ رابط المستودع الفعلي وأرفقه بالتسليم الجامعي.

للرفع باستخدام git من حزمة المستودع المحلية المرفقة:

```bash
git clone Hussein_Ahmed_Graduation_Project.bundle graduation-project
cd graduation-project
git remote remove origin
git remote add origin REPOSITORY_URL
git push -u origin main
```

استبدل REPOSITORY_URL بالرابط الذي يعرضه GitHub للمستودع الذي أنشأته. إذا رفعت محتويات المجلد مباشرة بدل استنساخ bundle، نفذ git init وgit add وgit commit أولًا.

المرفق .gitignore يستبعد النسخ الاحتياطية وملفات البيئة والكاش. قاعدة students.db المرفقة وهمية مخصصة للمناقشة. لا تستبدلها بقاعدة تشغيل حقيقية عند الرفع.
