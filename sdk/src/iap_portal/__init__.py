"""iap_portal — SDK + CLI for the IAP Portal for teams.

Two surfaces:
    from iap_portal.auth import current_user        # framework-agnostic auth
    from iap_portal.auth.fastapi import require_user
    from iap_portal.auth.flask import require_user

    # and the CLI:  `iap-portal init ...` / `iap-portal apply ...` / `iap-portal dev`
"""

from iap_portal.auth.core import User, current_user

__all__ = ["User", "current_user"]
__version__ = "0.1.0"
