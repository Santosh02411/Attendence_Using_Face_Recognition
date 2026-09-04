"""
Admin account recovery.

This script resets an admin's password (or creates a fresh one) directly
via server/database access — the unconditional fallback regardless of
whether email/SMTP is configured. A more restricted self-service
"forgot password" flow does exist for admin accounts (see
ADMIN_PASSWORD_RESET_ENABLED in config.py and set_admin_recovery_email.py),
but it's opt-in and depends on an admin having a recovery email on file;
this script always works.

Usage:
    python reset_admin_password.py <username> [--create]

Examples:
    # Reset the existing 'admin' account's password (prompts for a new one):
    python reset_admin_password.py admin

    # Create a brand-new admin account if you're completely locked out:
    python reset_admin_password.py recovery-admin --create
"""
import argparse
import getpass
import sys

from werkzeug.security import generate_password_hash

from app import get_db_connection, init_databases, validate_password_strength


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('username', help='Admin username to reset (or create)')
    parser.add_argument('--create', action='store_true', help='Create a new admin account instead of resetting an existing one')
    args = parser.parse_args()

    init_databases()
    conn = get_db_connection()

    existing = conn.execute('SELECT id FROM admins WHERE username=?', (args.username,)).fetchone()

    if args.create and existing:
        print(f'Admin account "{args.username}" already exists — omit --create to reset its password instead.')
        conn.close()
        sys.exit(1)
    if not args.create and not existing:
        print(f'No admin account named "{args.username}" found. Use --create to make a new one.')
        conn.close()
        sys.exit(1)

    password = getpass.getpass('New password: ')
    confirm = getpass.getpass('Confirm password: ')
    if password != confirm:
        print('Passwords do not match.')
        conn.close()
        sys.exit(1)

    error = validate_password_strength(password)
    if error:
        print(f'Password rejected: {error}')
        conn.close()
        sys.exit(1)

    hashed = generate_password_hash(password)
    if args.create:
        conn.execute('INSERT INTO admins(username, password) VALUES (?, ?)', (args.username, hashed))
        print(f'Created new admin account "{args.username}".')
    else:
        conn.execute('UPDATE admins SET password=?, failed_attempts=0, locked_until=NULL WHERE username=?',
                     (hashed, args.username))
        print(f'Password reset for admin account "{args.username}".')

    conn.commit()
    conn.close()


if __name__ == '__main__':
    main()
