"""tools/check_translations.py, the `make check-docs` check (#283).

The anchors and the emphasis rule are the two places the check reimplements
someone else's rendering — GitHub's heading ids and CommonMark's flanking
rules — so they are pinned here against the cases the translations hit.
"""

import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_translations as ct  # noqa: E402


class TestSlug:
    def test_english_heading(self):
        assert ct.slug("Portable mode (Windows)") == "portable-mode-windows"

    def test_code_and_punctuation_drop_out(self):
        assert ct.slug("Why not `keyhac --no-ui`?") == "why-not-keyhac---no-ui"
        assert ct.slug("Windows — the zip package") == "windows--the-zip-package"

    def test_japanese_letters_survive_and_brackets_go(self):
        assert ct.slug("インストール") == "インストール"
        assert ct.slug("「サーバーへ接続」の使い方（macOS）") == "サーバーへ接続の使い方macos"

    def test_link_text_is_kept(self):
        assert ct.slug("[Archives](ARCHIVE_FEATURE.md) and more") == "archives-and-more"


class TestAnchors:
    def test_duplicates_are_numbered_and_code_is_skipped(self, tmp_path):
        page = tmp_path / "page.md"
        page.write_text("# Title\n## Setup\n```\n# not a heading\n```\n## Setup\n"
                        '<a id="kept"></a>\n', encoding="utf-8")
        assert ct.anchors(page, {}) == {"title", "setup", "setup-1", "kept"}


class TestStuckEmphasis:
    def test_bold_link_straight_before_japanese_stays_literal(self):
        assert ct.stuck_emphasis("**[設定](x.md)**を参照")
        assert ct.stuck_emphasis("あ**`code`**い")

    def test_a_space_or_punctuation_after_it_closes(self):
        assert not ct.stuck_emphasis("**[設定](x.md)** を参照")
        assert not ct.stuck_emphasis("**作者の X（Twitter）**：リンク")
        assert not ct.stuck_emphasis("（**太字**）")

    def test_letters_on_both_sides_are_fine(self):
        assert not ct.stuck_emphasis("これは**太字**です")
        assert not ct.stuck_emphasis("*「保護」* と表示")

    def test_bullets_and_arithmetic_are_not_emphasis(self):
        assert not ct.stuck_emphasis("- item")
        assert not ct.stuck_emphasis("2 * 3 * 4")

    def test_escaped_stars_are_text(self):
        assert not ct.stuck_emphasis("| 表示\\* | x |")


def test_links_skip_code():
    text = ("[a](A.md) `[b](B.md)` [`e`](E.md)\n```\n[c](C.md)\n```\n"
            "<a href=\"D.md#x\">d</a>\n")
    assert ct.links(text) == ["A.md", "E.md", "D.md#x"]


class TestFix:
    """``--fix``: links copied from the English page, pointed at translations."""

    def make_repo(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ct, "REPO_ROOT", tmp_path)
        doc = tmp_path / "doc"
        doc.mkdir()
        (tmp_path / "README.md").write_text("# Keyhac\n\nSee [A](doc/A.md#setup).\n",
                                            encoding="utf-8")
        (tmp_path / "README.ja.md").write_text("# Keyhac\n\n[A](doc/A.md#setup) を参照。\n",
                                               encoding="utf-8")
        (doc / "A.md").write_text("# A\n\n## Intro\n\n## Setup\n\n[B](B.md#x)\n",
                                  encoding="utf-8")
        (doc / "A.ja.md").write_text(
            "# A\n\n## はじめに\n\n## 準備\n\n[B](B.md#x) と [準備](#setup)、"
            "`[code](A.md)`、[README](../README.md)、[`B`](#intro)\n", encoding="utf-8")
        (doc / "B.md").write_text("# B\n\n## X\n", encoding="utf-8")
        return tmp_path

    def test_links_and_anchors_move_to_the_translations(self, tmp_path, monkeypatch):
        root = self.make_repo(tmp_path, monkeypatch)
        assert ct.fix() == 0
        readme = (root / "README.ja.md").read_text(encoding="utf-8")
        assert readme == ("# Keyhac\n\n[English](README.md) \\| 日本語\n\n"
                          "[A](doc/A.ja.md#準備) を参照。\n")
        page = (root / "doc" / "A.ja.md").read_text(encoding="utf-8")
        assert "[English](A.md) \\| 日本語" in page
        assert "[B](B.md#x)" in page                  # B has no translation
        assert "[準備](#準備)" in page                 # its own anchor, mapped
        assert "`[code](A.md)`" in page               # code is left alone
        assert "[README](../README.ja.md)" in page
        assert "[`B`](#はじめに)" in page               # code in the link text

    def test_the_english_page_gets_its_switcher_under_the_title(self, tmp_path, monkeypatch):
        root = self.make_repo(tmp_path, monkeypatch)
        ct.fix()
        assert (root / "doc" / "A.md").read_text(encoding="utf-8").startswith(
            "# A\n\nEnglish \\| [日本語](A.ja.md)\n\n## Intro\n")

    def test_a_heading_mismatch_is_reported_not_guessed(self, tmp_path, monkeypatch):
        root = self.make_repo(tmp_path, monkeypatch)
        (root / "doc" / "A.ja.md").write_text("# A\n\n## 準備\n\n[x](#setup)\n",
                                             encoding="utf-8")
        assert ct.fix() == 1


def test_generated_pages_are_not_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "REPO_ROOT", tmp_path)
    (tmp_path / "doc").mkdir()
    for name in ("README.md", "doc/installation.md", "doc/installation.ja.md",
                 "doc/config-api.md", "doc/action-api.md", "doc/config-py-template.md"):
        (tmp_path / name).write_text("# x\n", encoding="utf-8")
    assert [p.relative_to(tmp_path).as_posix() for p in ct.sources()] == [
        "README.md", "doc/installation.md"]
