"""Console stream redirection (issue #1: print() must reach the console)."""

import logging
import sys

from keyhac.core.log import Console, _ConsoleStream, redirect_std_streams


class TestConsoleStream:

    def _console(self):
        console = Console()
        console.mirror_stderr = False
        return console

    def test_print_lines(self):
        console = self._console()
        stream = _ConsoleStream(console, 100)
        print("hello", file=stream)
        print("a", "b", file=stream)
        assert [text for text, _level in console.lines()] == ["hello", "a b"]

    def test_line_buffering_joins_partial_writes(self):
        console = self._console()
        stream = _ConsoleStream(console, 100)
        stream.write("par")
        assert console.lines() == []          # no newline yet
        stream.write("tial\nnext")
        assert [text for text, _ in console.lines()] == ["partial"]
        stream.flush()
        assert [text for text, _ in console.lines()] == ["partial", "next"]

    def test_empty_print_makes_blank_line(self):
        console = self._console()
        stream = _ConsoleStream(console, 100)
        print(file=stream)
        assert [text for text, _ in console.lines()] == [""]

    def test_level_carried_to_lines(self):
        console = self._console()
        stream = _ConsoleStream(console, logging.ERROR)
        print("boom", file=stream)
        assert console.lines() == [("boom", logging.ERROR)]


class TestRedirectStdStreams:

    def test_redirect_and_no_mirror_recursion(self):
        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            redirect_std_streams()
            console = Console.get_instance()
            console.pull_lines()  # drop anything logged before this test
            before = len(console.lines())
            print("via stdout")
            lines = [text for text, _ in console.lines()]
            assert lines[before:] == ["via stdout"]
            # The mirror must not write back into the redirected stderr
            assert console._mirror_stream is not sys.stderr
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
