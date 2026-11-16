"""Tests for web tools (offline paths), todo tool, and search_agent wiring."""
from __future__ import annotations

import json

from dsc.tools.web import _DDGParser, _assert_safe_url, html_to_text, web_fetch, web_search
from dsc.tools.todo import todo_write
from dsc.tools import HANDLERS, SCHEMAS, tool_schemas


class TestSSRFGuard:
    def test_non_http_refused(self):
        assert "only http(s)" in web_fetch(url="ftp://example.com/x")

    def test_localhost_refused(self):
        assert "blocked" in web_fetch(url="http://localhost:8080/")

    def test_private_ip_refused(self):
        assert "blocked" in web_fetch(url="http://10.0.0.1/admin")

    def test_metadata_ip_refused(self):
        assert "blocked" in web_fetch(url="http://169.254.169.254/latest/meta-data/")

    def test_missing_url(self):
        assert web_fetch().startswith("error:")


class TestHtmlToText:
    def test_title_and_text(self):
        title, text = html_to_text(
            "<html><head><title>Hi</title></head><body>"
            "<script>var junk=1</script><p>Hello <b>world</b></p></body></html>"
        )
        assert title == "Hi"
        assert "Hello" in text and "world" in text
        assert "junk" not in text


class TestDDGParser:
    def test_parses_fixture(self):
        html = """
        <div class="result results_links">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=abc">Example A</a>
          <a class="result__snippet" href="#">The snippet text here.</a>
        </div>
        """
        p = _DDGParser()
        p.feed(html)
        assert len(p.results) == 1
        assert p.results[0]["url"] == "https://example.com/a"
        assert p.results[0]["title"] == "Example A"
        assert "snippet" in p.results[0]["snippet"]


class TestTodo:
    def test_wholesale_replace_and_counts(self):
        state = {}
        r = todo_write(state, todos=[
            {"content": "a", "status": "completed"},
            {"content": "b", "status": "in_progress"},
        ])
        assert "updated" in r and state["todos"][0]["status"] == "completed"

    def test_multiple_in_progress_refused(self):
        r = todo_write({}, todos=[
            {"content": "a", "status": "in_progress"},
            {"content": "b", "status": "in_progress"},
        ])
        assert "exactly one" in r

    def test_bad_status(self):
        assert "invalid status" in todo_write({}, todos=[{"content": "x", "status": "nope"}])

    def test_missing_todos(self):
        assert todo_write({}).startswith("error:")


class TestRegistry:
    def test_all_registered(self):
        for name in ("web_fetch", "web_search", "todo_write", "search_agent"):
            assert name in HANDLERS and name in SCHEMAS

    def test_schemas_valid_shape(self):
        schemas = tool_schemas(sorted(HANDLERS))
        for s in schemas:
            assert s["type"] == "function"
            fn = s["function"]
            assert fn["name"] and fn["parameters"]["type"] == "object"

    def test_todo_schema_matches(self):
        s = SCHEMAS["todo_write"]["function"]["parameters"]
        assert s["properties"]["todos"]["type"] == "array"
