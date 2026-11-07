#!/usr/bin/env python3
"""Safe, repeatable deploy helper for the EXISTING Hussein Ahmed PythonAnywhere app.

This script intentionally cannot log in to PythonAnywhere. Run it from an
authenticated Bash console in the existing account. It NEVER copies any
SQLite database from GitHub or deletes user data.
"""
from __future__ import annotations

import argparse
import datetime as dt
from collections import Counter
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY = "https://github.com/husen2004/hussein-ahmed-graduation-project.git"
APP_FILES = (
    "server.py", "index.html", "wsgi.py", "requirements.txt",
    "database/init_db.py", "database/regrade_by_birthyear.py",
    "assets/school_logo.png",
    "tools/set_admin_password.py", "tools/create_demo_data.py",
    "tools/migrate_august_to_september_2026.py",
    "tests/test_system.py", "tools/deploy_existing_pythonanywhere.py",
)
DEFAULT_WSGI = Path("/var/www/hussin2004_pythonanywhere_com_wsgi.py")
DEFAULT_HEALTH = "https://hussin2004.pythonanywhere.com/api/health"


def die(msg):
    raise SystemExit("ERROR: " + str(msg))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, cwd=None, env=None):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def validate_project(project, db_path):
    if not project.is_dir() or not (project / "server.py").is_file():
        die(f"Existing project folder missing or not recognized: {project}")
    if not (project / "wsgi.py").is_file():
        die("wsgi.py missing: verify the existing PythonAnywhere source directory")
    if not db_path.is_file():
        die(f"Existing database is absent: {db_path}. Pass --database if external.")
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=30) as conn:
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            die(f"Database quick_check failed: {integrity}")
        count = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    print(f"Existing project: {project}")
    print(f"Existing database: {db_path} ({count} SQLite tables, quick_check OK)")
    print("Database copy from GitHub: FORBIDDEN by this script")


def get_source(source_dir, workspace):
    if source_dir:
        src = source_dir.resolve()
        if not (src / "server.py").is_file():
            die(f"--source-dir must point to the project root: {src}")
        revision = "local-verified-source"
    else:
        src = workspace / "github-main"
        run(["git", "clone", "--depth", "1", "--branch", "main", REPOSITORY, str(src)])
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=src, text=True).strip()
    for rel in APP_FILES:
        if not (src / rel).is_file():
            die(f"Source missing: {rel}")
    return src, revision


def smoke_source(src):
    # This test is isolated from the existing hosted database. The integration
    # suite itself uses TemporaryDirectory for per-test SQLite databases.
    env = os.environ.copy()
    env["AFAQ_DB_PATH"] = str(src / "database" / "students.db")
    env["AFAQ_BACKUP_DIR"] = str(src / "database" / "staging_backups")
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=src, env=env)
    for rel in ("server.py", "wsgi.py", "database/init_db.py", "tests/test_system.py"):
        with open(src / rel, "rb") as f:
            compile(f.read(), rel, "exec")



def key_data_state(path):
    """Fingerprint existing business rows, ignoring later-added schema columns."""
    protected = (
        "Students", "Parents", "ParentStudents", "Enrollments", "Users",
        "MonthlyFees", "Fees", "FeePayments", "Receipts", "CashDebts",
        "CashDebtPayments", "Attendance", "Marks", "CommunicationCampaigns",
        "CommunicationRecipients", "StudentClassMemberships",
    )
    out = {}
    with sqlite3.connect(path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in protected:
            if table not in tables:
                continue
            cols = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            out[table] = cols
    return out


def fingerprint_table(path, table, columns):
    # Compare rows using only the original columns; migration-added columns
    # should not cause a false alarm. Any changed/new/deleted row DOES.
    names = ",".join('"' + c.replace('"', '""') + '"' for c in columns)
    row_digests = []
    with sqlite3.connect(path) as conn:
        for row in conn.execute(f'SELECT {names} FROM "{table}"'):
            record = json.dumps([v.hex() if isinstance(v, bytes) else v for v in row],
                                ensure_ascii=False, default=str, separators=(",", ":"))
            row_digests.append(hashlib.sha256(record.encode("utf-8")).hexdigest())
    return Counter(row_digests)


def rehearse_live_migrations(src, live_db, workspace):
    """Run init_database on a temporary backup, never on real hosted data."""
    test_db = workspace / "migration_rehearsal.sqlite3"
    db_backup(live_db, test_db)
    original_tables = key_data_state(test_db)
    before = {table: fingerprint_table(test_db, table, cols)
              for table, cols in original_tables.items()}
    env = os.environ.copy()
    env["AFAQ_DB_PATH"] = str(test_db)
    env["AFAQ_BACKUP_DIR"] = str(workspace / "migration_rehearsal_backups")
    print("Rehearsing database initialization on an isolated copy of live SQLite...")
    run([sys.executable, "-c", "from database.init_db import init_database; init_database()"], cwd=src, env=env)
    with sqlite3.connect(test_db) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            die("Database migration rehearsal did not pass integrity_check")
    after_tables = key_data_state(test_db)
    changed = []
    for table, columns in original_tables.items():
        if table not in after_tables or not set(columns).issubset(after_tables[table]):
            changed.append(table + " (removed table/column)")
            continue
        updated_rows = fingerprint_table(test_db, table, columns)
        if table == "MonthlyFees":
            # On startup the existing application generates this month's new
            # invoices. Permit only inserts; protect all prior invoice values.
            if any(updated_rows[d] < old_count for d, old_count in before[table].items()):
                changed.append(table + " (previous fee values removed/modified)")
            elif sum(updated_rows.values()) > sum(before[table].values()):
                print("NOTICE:", sum(updated_rows.values()) - sum(before[table].values()),
                      "new monthly fee rows would be generated at reload (existing app behavior).")
        elif updated_rows != before[table]:
            changed.append(table)
    if changed:
        die("Database initialization would alter existing business records in "
            + ", ".join(changed) + ". No live deployment was performed. "
            "Review migrations manually before updating the live account.")
    print("Database migration rehearsal passed: existing business records unchanged.")

def db_backup(source, destination):
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30) as input_db:
        with sqlite3.connect(destination) as output_db:
            input_db.backup(output_db)
    with sqlite3.connect(destination) as c:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            die("The SQLite backup did not pass integrity_check")


def copy_atomic(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".incoming_deploy")
    shutil.copy2(source, temp)
    os.replace(temp, destination)


def reload_and_check(wsgi_file, reload, url):
    if not reload:
        print("Reload NOT requested. Use PythonAnywhere > Web > Reload after deployment.")
        return
    if not wsgi_file.is_file():
        print(f"WSGI file not found ({wsgi_file}). Use Web > Reload manually.")
        return
    # PythonAnywhere supports touching the WSGI entrypoint as a reload trigger.
    wsgi_file.touch()
    print(f"Triggered WSGI reload: {wsgi_file}")
    try:
        with urllib.request.urlopen(url, timeout=12) as response:
            data = json.loads(response.read(8192).decode("utf-8"))
            ok = response.status == 200 and data.get("status") == "ok"
            print("Public health endpoint:", "OK (HTTP 200)" if ok else f"UNEXPECTED: {data}")
    except (OSError, ValueError, urllib.error.HTTPError) as e:
        print("Public health endpoint NOT verified:", type(e).__name__, str(e))
        print("Open the Web tab to check error/server logs and Reload if necessary.")


def restore_from(backup, reload, wsgi, url):
    manifest_path = backup / "manifest.json"
    if not manifest_path.is_file():
        die(f"No backup manifest at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    project = Path(manifest["project"]).resolve()
    for rel, existed in manifest["previous_files"].items():
        dest = project / rel
        if existed:
            old = backup / "files" / rel
            if not old.is_file():
                die(f"Missing original file in backup: {old}")
            copy_atomic(old, dest)
        else:
            dest.unlink(missing_ok=True)
    print("Restored application CODE from:", backup)
    print("The production database was not restored/replaced by this rollback.")
    print("Database snapshot in backup is preserved for manual recovery only.")
    reload_and_check(wsgi, reload, url)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Existing project root (source directory currently used by WSGI)")
    parser.add_argument("--database", help="Live SQLite database path if different from <project>/database/students.db")
    parser.add_argument("--source-dir", help="Optional tested local source directory, for offline rehearsals")
    parser.add_argument("--apply", action="store_true", help="Apply code updates after backup and tests")
    parser.add_argument("--rollback", help="Backup folder to restore from (restores code only)")
    parser.add_argument("--reload", action="store_true", help="Touch WSGI file after deploy/rollback")
    parser.add_argument("--wsgi-file", default=str(DEFAULT_WSGI))
    parser.add_argument("--health-url", default=DEFAULT_HEALTH)
    args = parser.parse_args()
    wsgi = Path(args.wsgi_file)
    if args.rollback:
        restore_from(Path(args.rollback).expanduser().resolve(), args.reload, wsgi, args.health_url)
        return
    if not args.project:
        die("Provide --project. Check the PROJECT_HOME path in your PythonAnywhere Web > WSGI file first.")
    project = Path(args.project).expanduser().resolve()
    db_path = (Path(args.database).expanduser().resolve() if args.database
               else project / "database" / "students.db")
    validate_project(project, db_path)
    with tempfile.TemporaryDirectory(prefix="hussein-pa-staging-") as d:
        src, revision = get_source(Path(args.source_dir) if args.source_dir else None, Path(d))
        print("Source revision:", revision)
        for rel in APP_FILES:
            original = project / rel
            if not original.is_file():
                print("ADD    ", rel)
            elif sha256(original) != sha256(src / rel):
                print("CHANGE ", rel)
            else:
                print("SAME   ", rel)
        print("Running staged integration tests against isolated test data...")
        smoke_source(src)
        rehearse_live_migrations(src, db_path, Path(d))
        if not args.apply:
            print("READ-ONLY CHECK PASSED. No hosted application files were changed.")
            return
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = project.parent / (project.name + "_rollback_" + timestamp)
        backup.mkdir(mode=0o700)
        previous = {}
        print("Backing up live code and SQLite data to", backup)
        for rel in APP_FILES:
            current = project / rel
            previous[rel] = current.is_file()
            if current.is_file():
                old = backup / "files" / rel
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(current, old)
        db_backup(db_path, backup / "live_before_deploy.sqlite3")
        manifest = {"project": str(project), "live_db": str(db_path),
                    "previous_files": previous, "revision": revision,
                    "created_utc": timestamp}
        (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            for rel in APP_FILES:
                copy_atomic(src / rel, project / rel)
                if sha256(src / rel) != sha256(project / rel):
                    die(f"File checksum mismatch after update: {rel}")
        except Exception:
            print("Copy failed; restoring application code from backup...")
            restore_from(backup, False, wsgi, args.health_url)
            raise
    print("Code update completed. The existing SQLite database file was not replaced.")
    print("Rollback command:")
    print(f"  python3 tools/deploy_existing_pythonanywhere.py --rollback '{backup}' --reload")
    reload_and_check(wsgi, args.reload, args.health_url)


if __name__ == "__main__":
    main()
