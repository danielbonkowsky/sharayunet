"""
Run this script once to generate the ADMIN_PASSWORD_HASH value for your .env file.

Usage:
    python setup_admin.py
"""
from werkzeug.security import generate_password_hash
import getpass

password = getpass.getpass("Enter admin password: ")
confirm = getpass.getpass("Confirm password: ")

if password != confirm:
    print("Passwords do not match.")
    raise SystemExit(1)

hashed = generate_password_hash(password)
print("\nAdd this to your .env file:")
print(f"ADMIN_PASSWORD_HASH={hashed}")
