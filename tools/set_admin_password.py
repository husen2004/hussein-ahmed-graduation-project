from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import secrets
import sqlite3
from pathlib import Path


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Change a project user password safely.")
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--database",
        default=os.environ.get(
            "AFAQ_DB_PATH",
            str(Path(__file__).resolve().parents[1] / "database" / "students.db"),
        ),
    )
    args = parser.parse_args()

    first = getpass.getpass(f"New password for {args.username}: ")
    second = getpass.getpass("Repeat the new password: ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    if len(first) < 10:
        raise SystemExit("Use at least 10 characters for a hosted account.")

    database = Path(args.database).expanduser().resolve()
    if not database.is_file():
        raise SystemExit(f"Database not found: {database}")

    conn = sqlite3.connect(database, timeout=30)
    try:
        result = conn.execute(
            "UPDATE Users SET PasswordHash=? WHERE Username=?",
            (password_hash(first), args.username),
        )
        if result.rowcount != 1:
            raise SystemExit(f"User not found: {args.username}")
        conn.execute("DELETE FROM UserSessions WHERE UserID=(SELECT UserID FROM Users WHERE Username=?)", (args.username,))
        conn.commit()
    finally:
        conn.close()
    print(f"Password changed for {args.username}; previous sessions were removed.")


if __name__ == "__main__":
    main()
