"""
app/auth/password.py
=====================
Password hashing and verification using bcrypt.

WHY BCRYPT?
-----------
You must NEVER store passwords as plain text.
If your database is stolen, all user passwords are exposed.

Bcrypt solves this by:
1. Hashing the password with a random "salt"
   (salt = random data mixed in so two identical passwords
    produce different hashes — prevents rainbow table attacks)

2. Being intentionally slow (work factor = 12 rounds)
   This makes brute-force attacks very expensive.
   Even if someone steals the hash, cracking it takes years.

HOW IT WORKS:
-------------
Registration:
  plain password "MyPass123" → bcrypt → "$2b$12$randomsalt...hashedvalue"
  Store the hash in the database, throw away the plain password.

Login:
  User types "MyPass123"
  We call verify_password("MyPass123", stored_hash)
  bcrypt re-runs the hash and compares — returns True or False
  We NEVER decrypt the hash — there is no decryption.

USAGE:
------
    from app.auth.password import hash_password, verify_password

    hashed = hash_password("MyPass123")
    is_correct = verify_password("MyPass123", hashed)   # True
    is_wrong   = verify_password("WrongPass", hashed)   # False
"""

import bcrypt


def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password using bcrypt.

    Returns a string like:
    "$2b$12$N9qo8uLOickgx2ZMRZoMyeIjZAgcfl7p92ldGxad68LJZdL17lhWy"

    This string contains:
    - $2b$   → bcrypt algorithm version
    - 12$    → work factor (12 rounds of hashing)
    - N9qo...→ 22-char random salt
    - jZAg...→ the actual hash

    Arguments:
    ----------
    plain_password : the password typed by the user (e.g. "MyPass123")

    Returns:
    --------
    hashed string to store in the database
    """

    # encode() converts the string to bytes (bcrypt needs bytes)
    password_bytes = plain_password.encode("utf-8")

    # gensalt() generates a new random salt each time
    # rounds=12 means 2^12 = 4096 iterations — slow by design
    salt = bcrypt.gensalt(rounds=12)

    hashed = bcrypt.hashpw(password_bytes, salt)

    # decode back to string for storage in PostgreSQL TEXT column
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Check if a plain-text password matches a stored bcrypt hash.

    This is called during login:
      1. User submits their password
      2. We fetch the stored hash from the DB
      3. This function checks if they match

    Returns True if the password is correct, False otherwise.
    NEVER raises an exception for wrong password — just returns False.

    Arguments:
    ----------
    plain_password   : what the user typed in the login form
    hashed_password  : what is stored in the users table

    Returns:
    --------
    True  → password is correct
    False → password is wrong
    """

    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        # Any error (malformed hash, etc.) → treat as wrong password
        return False