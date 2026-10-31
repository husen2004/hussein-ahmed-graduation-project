"""Paste the relevant lines into the PythonAnywhere WSGI configuration file.

Replace YOUR_USERNAME and the folder name if you upload the project elsewhere.
The public URL is normally https://YOUR_USERNAME.pythonanywhere.com/.
"""
import os
import sys


PROJECT_HOME = "/home/YOUR_USERNAME/afaq-school-system"

if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

os.environ["AFAQ_CLOUD"] = "1"
os.environ["AFAQ_SQLITE_WAL"] = "1"
os.environ["AFAQ_DB_PATH"] = os.path.join(PROJECT_HOME, "database", "students.db")
os.environ["AFAQ_BACKUP_DIR"] = os.path.join(PROJECT_HOME, "database", "backups")

from wsgi import application  # noqa: E402,F401
