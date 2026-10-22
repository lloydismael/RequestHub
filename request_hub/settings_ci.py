"""Settings for disposable test runs (CI and local ``manage.py test``).

Never use this module for a deployed process. Only the password hasher is
changed; PBKDF2 costs hundreds of milliseconds per user creation or login and
dominated the test suite's runtime.
"""

from .settings import *  # noqa: F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
