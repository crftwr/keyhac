"""Configuration file loading (ported from keyhac-mac keyhac_config.py)."""

import os

from keyhac.core import permissions


class Config:
    """Loads ~/.keyhac/config.py, copying the template on first run."""

    def __init__(self, config_path: str, template_path: str):

        self.config_path = config_path

        if not os.path.exists(config_path):
            permissions.ensure_private_dir(os.path.dirname(config_path))
            permissions.copy_private(template_path, config_path)

        with open(config_path, "r", encoding="utf-8") as f:
            source = f.read()

        code = compile(source, config_path, "exec")
        self.namespace = {"__file__": config_path, "__name__": "__config__"}
        exec(code, self.namespace)

    def call(self, name: str, *args):
        """Call a function defined in the config file, if it exists."""
        func = self.namespace.get(name)
        if func is not None:
            return func(*args)
