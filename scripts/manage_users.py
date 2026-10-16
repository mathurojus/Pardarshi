"""Operator tool for accounts (there is no email service, so this is the password-reset path).

  python -m scripts.manage_users list
  python -m scripts.manage_users reset someone@example.com      # prompts for the new password
  python -m scripts.manage_users delete someone@example.com
"""
from __future__ import annotations

import getpass
import sys
import time

from app import auth


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("list", "reset", "delete"):
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "list":
        for r in auth.q("SELECT id, email, name, is_guest, created_at FROM users ORDER BY id").fetchall():
            kind = "guest" if r["is_guest"] else "user "
            print(f"{r['id']:>4}  {kind}  {r['email']:<36} {r['name']:<24} {time.strftime('%Y-%m-%d', time.localtime(r['created_at']))}")
        return
    email = sys.argv[2].strip().lower()
    u = auth.q("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u:
        print("No such user.")
        return
    if cmd == "reset":
        pw = getpass.getpass("New password: ")
        problem = auth.password_problem(pw, email, u["name"])
        if problem:
            print(problem)
            return
        auth.q("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_password(pw), u["id"]))
        auth.q("DELETE FROM sessions WHERE user_id=?", (u["id"],))
        print("Password updated; all sessions signed out.")
    else:
        with auth._db_lock:
            auth._delete_user_data(auth.db(), u["id"])
        print("User and their audits deleted.")


if __name__ == "__main__":
    main()
