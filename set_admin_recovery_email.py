"""
Set (or clear) an admin account's recovery email.

This is deliberately a server-access-only operation — there is no HTTP
route anywhere in this application that can set or change
admins.recovery_email. That's intentional: the self-service admin
password reset flow (see README's "Self-Service Password Reset" ->
"Admin Accounts", and ADMIN_PASSWORD_RESET_ENABLED in config.py) only
ever emails a reset link to whatever address is already on file — if a
web route could change that address, a compromised admin session, a
CSRF gap, or an XSS bug could quietly redirect future reset links to an
attacker, which would defeat the point of a "recovery" mechanism.
Requiring direct server/database access (e.g. via SSH) to set this
address keeps it outside that attack surface, same as
reset_admin_password.py already does for the password itself.

Usage:
    python set_admin_recovery_email.py <username> <email>
    python set_admin_recovery_email.py <username> --clear

Examples:
    python set_admin_recovery_email.py admin admin@school.edu
    python set_admin_recovery_email.py admin --clear
"""
import argparse
import re
import sys

from app import get_db_connection, init_databases

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('username', help='Admin username to update')
    parser.add_argument('email', nargs='?', help='Recovery email to set')
    parser.add_argument('--clear', action='store_true', help='Remove the recovery email instead of setting one')
    args = parser.parse_args()

    if not args.clear and not args.email:
        print('Provide an email address, or use --clear to remove the existing one.')
        sys.exit(1)
    if args.clear and args.email:
        print('Provide either an email address or --clear, not both.')
        sys.exit(1)
    if args.email and not _EMAIL_RE.match(args.email):
        print(f'"{args.email}" does not look like a valid email address.')
        sys.exit(1)

    init_databases()
    conn = get_db_connection()
    admin = conn.execute('SELECT id FROM admins WHERE username=?', (args.username,)).fetchone()
    if not admin:
        print(f'No admin account named "{args.username}" found.')
        conn.close()
        sys.exit(1)

    new_value = None if args.clear else args.email
    conn.execute('UPDATE admins SET recovery_email=? WHERE username=?', (new_value, args.username))
    conn.commit()
    conn.close()

    if args.clear:
        print(f'Cleared the recovery email for admin account "{args.username}". '
              f'Self-service password reset is now unavailable for this account until one is set again.')
    else:
        print(f'Set the recovery email for admin account "{args.username}" to {args.email}.')


if __name__ == '__main__':
    main()
