"""tools/collect_dependencies.py — the RECORD-walking file filter that decides
what lands in a bundle. A wrong answer here is expensive in both directions:
dropping a runtime file breaks the shipped app, and keeping a test suite pays
for it in bundle size and per-binary codesigning."""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tools"))

import collect_dependencies  # noqa: E402

_skip = collect_dependencies._should_skip_file


class TestShouldSkipFile:

    def test_runtime_package_files_are_kept(self):
        assert not _skip(Path("objc/_objc.cpython-313-darwin.so"))
        assert not _skip(Path("PyObjCTools/AppHelper.py"))
        assert not _skip(Path("PIL/Image.py"))

    def test_dist_info_is_kept(self):
        # generate_third_party_notices.py reads these for the license text
        assert not _skip(Path("pyobjc_core-12.2.1.dist-info/METADATA"))

    def test_paths_outside_site_packages_are_skipped(self):
        assert _skip(Path("../../../bin/some-script"))

    def test_editable_install_shims_are_skipped(self):
        assert _skip(Path("puikit.pth"))
        assert _skip(Path("__editable___puikit_finder.py"))

    def test_pyobjc_test_suite_is_skipped(self):
        # Owned by pyobjc-core's RECORD, so it cannot be dropped per
        # distribution; 140 .so files and their .dSYM bundles ride on it.
        assert _skip(Path("PyObjCTest/test_methods.py"))
        assert _skip(Path("PyObjCTest/arrayint.cpython-313-darwin.so"))
        assert _skip(Path("PyObjCTest/NULL.cpython-313-darwin.so.dSYM/"
                          "Contents/Resources/DWARF/NULL.cpython-313-darwin.so"))


class _Dist:
    """The slice of importlib.metadata.Distribution verify_versions reads."""

    def __init__(self, name, version, requires=None):
        self.metadata = {"Name": name}
        self.version = version
        self.requires = requires


class TestVerifyVersions:

    def _verify(self, tmp_path, dependencies, dists):
        pyproject = tmp_path / "pyproject.toml"
        listed = ", ".join(f"'{d}'" for d in dependencies)  # TOML literal strings
        pyproject.write_text(f"[project]\ndependencies = [{listed}]\n")
        resolved = {collect_dependencies._canonical(d.metadata["Name"]): d for d in dists}
        return collect_dependencies.verify_versions(str(pyproject), resolved)

    def test_satisfied_pins_pass(self, tmp_path):
        assert self._verify(tmp_path, ["puikit>=1.15.2"], [_Dist("puikit", "1.15.2")])

    def test_venv_older_than_the_pin_fails(self, tmp_path):
        # 2.7.0's first Windows zip: the venv had 1.15.1 against >=1.15.2.
        assert not self._verify(tmp_path, ["puikit>=1.15.2"], [_Dist("puikit", "1.15.1")])

    def test_missing_declared_requirement_fails(self, tmp_path):
        assert not self._verify(tmp_path, ["puikit>=1.15.2"], [])

    def test_off_platform_requirement_is_ignored(self, tmp_path):
        assert self._verify(tmp_path, ['nosuchdist>=1; sys_platform == "nosuchos"'], [])

    def test_transitive_requirement_is_checked(self, tmp_path):
        dists = [_Dist("puikit", "1.15.2", ["numpy>=2"]), _Dist("numpy", "1.26.0")]
        assert not self._verify(tmp_path, ["puikit>=1.15.2"], dists)

    def test_extra_only_requirement_is_ignored(self, tmp_path):
        dists = [_Dist("puikit", "1.15.2", ['numpy>=2; extra == "fast"']),
                 _Dist("numpy", "1.26.0")]
        assert self._verify(tmp_path, ["puikit>=1.15.2"], dists)


class _InstalledDist(_Dist):
    """A _Dist with the PEP 610 direct_url.json a non-index install carries."""

    def __init__(self, name, version, direct_url=None, requires=None):
        super().__init__(name, version, requires)
        self._direct_url = direct_url

    def read_text(self, filename):
        if filename == "direct_url.json" and self._direct_url is not None:
            return json.dumps(self._direct_url)
        return None


_EDITABLE = {"url": "file:///Users/dev/puikit", "dir_info": {"editable": True}}
_LOCAL_WHEEL = {"url": "file:///tmp/puikit-1.17.0-py3-none-any.whl", "archive_info": {}}
_VCS = {"url": "https://github.com/x/y", "vcs_info": {"vcs": "git", "commit_id": "abc"}}


class TestVerifySources:

    def _verify(self, monkeypatch, dists, allow=""):
        monkeypatch.setenv("KEYHAC_BUNDLE_ALLOW_LOCAL", allow)
        resolved = {collect_dependencies._canonical(d.metadata["Name"]): d for d in dists}
        return collect_dependencies.verify_sources(resolved)

    def test_index_installs_pass(self, monkeypatch):
        assert self._verify(monkeypatch, [_InstalledDist("puikit", "1.17.0"),
                                          _InstalledDist("numpy", "2.1.0")]) == (True, set())

    def test_editable_puikit_is_refused(self, monkeypatch):
        # A ../puikit checkout would ship as it stands, released or not.
        ok, _ = self._verify(monkeypatch, [_InstalledDist("puikit", "1.17.0", _EDITABLE)])
        assert not ok

    def test_local_wheel_and_vcs_are_refused(self, monkeypatch):
        assert not self._verify(monkeypatch, [_InstalledDist("puikit", "1.17.0", _LOCAL_WHEEL)])[0]
        assert not self._verify(monkeypatch, [_InstalledDist("numpy", "2.1.0", _VCS)])[0]

    def test_allowed_editable_puikit_passes_and_goes_unversioned(self, monkeypatch):
        ok, unversioned = self._verify(
            monkeypatch, [_InstalledDist("PuiKit", "1.17.0", _EDITABLE)], allow="puikit")
        assert ok and unversioned == {"puikit"}

    def test_allowed_local_wheel_keeps_its_version_check(self, monkeypatch):
        ok, unversioned = self._verify(
            monkeypatch, [_InstalledDist("puikit", "1.17.0", _LOCAL_WHEEL)], allow="puikit")
        assert ok and unversioned == set()

    def test_other_editable_is_refused_even_when_allowed(self, monkeypatch):
        # Its RECORD holds only the shim, so the package would not be copied.
        ok, _ = self._verify(monkeypatch, [_InstalledDist("numpy", "2.1.0", _EDITABLE)],
                             allow="numpy")
        assert not ok

    def test_skipped_distribution_goes_unchecked(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\ndependencies = ['puikit>=1.17.0']\n")
        resolved = {"puikit": _Dist("puikit", "1.15.0")}
        assert collect_dependencies.verify_versions(str(pyproject), resolved, skip={"puikit"})
        assert not collect_dependencies.verify_versions(str(pyproject), resolved)
