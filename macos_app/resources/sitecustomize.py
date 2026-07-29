"""
Site customization for the bundled Keyhac Python.

This module is automatically imported by Python's site module during startup.
It disables user site-packages to ensure the bundled app is self-contained.
"""

import site
import sys

# Disable user site-packages for bundled app
site.ENABLE_USER_SITE = False

# Remove user site-packages from sys.path if it was already added
user_site = site.USER_SITE
if user_site in sys.path:
    sys.path.remove(user_site)
