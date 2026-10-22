"""HTTPS verified by the operating system (issue #279): ``keyhac.tls``.

The real ``truststore.inject_into_ssl`` replaces ``ssl.SSLContext`` for the
whole process, so these tests hand ``use_os_trust_store`` a stand-in module
and never touch ``ssl``.
"""

import inspect
import sys
import types

import pytest

from keyhac import main, tls


@pytest.fixture
def fake_truststore(monkeypatch):
    calls = []
    module = types.SimpleNamespace(inject_into_ssl=lambda: calls.append("inject"))
    monkeypatch.setitem(sys.modules, "truststore", module)
    return calls


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_macos_and_windows_verify_through_the_os(monkeypatch, fake_truststore, platform):
    monkeypatch.setattr(tls.sys, "platform", platform)
    assert tls.use_os_trust_store() is True
    assert fake_truststore == ["inject"]


def test_other_platforms_keep_openssl(monkeypatch, fake_truststore):
    monkeypatch.setattr(tls.sys, "platform", "linux")
    assert tls.use_os_trust_store() is False
    assert fake_truststore == []


def test_without_truststore_it_says_so_and_goes_on(monkeypatch):
    monkeypatch.setattr(tls.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "truststore", None)   # import raises
    warnings = []
    monkeypatch.setattr(tls.logger, "warning", warnings.append)
    assert tls.use_os_trust_store() is False
    assert "pip install truststore" in warnings[0]


def test_main_calls_it_before_anything_else():
    """It must run before any HTTPS client builds a context - `keyhac deps`
    downloads pip, and config.py may fetch at load."""
    body = inspect.getsource(main.main)
    assert body.index("use_os_trust_store()") < body.index("deps_main")
