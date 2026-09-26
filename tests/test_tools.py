import json

from tools import (
    TOOL_REGISTRY, TOOL_SCHEMAS, calculator, data_analyst, datetime_tool,
    execute_tool, file_reader, python_executor,
)


def test_registry_matches_schemas():
    assert {s["function"]["name"] for s in TOOL_SCHEMAS} == set(TOOL_REGISTRY)


def test_calculator():
    assert calculator("42 * 17") == "714"
    assert calculator("sqrt(144) + 10") == "22.0"
    assert calculator("1/0") == "Error: division by zero"
    assert "unknown functions" in calculator("__import__('os')")


def test_python_executor_runs_and_blocks():
    assert "Output:\n6" in python_executor("print(sum([1, 2, 3]))")
    assert "Blocked modules" in python_executor("import os\nprint(os.getcwd())")
    assert "Execution error" in python_executor("open('x.txt', 'w')")


def test_data_analyst_json_and_csv():
    out = data_analyst(json.dumps([{"a": 1}, {"a": 3}]))
    assert "**Records:** 2" in out and "mean=2" in out
    assert "unique=2" in data_analyst("name\nx\ny", data_format="csv")


def test_file_reader_restrictions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "note.txt").write_text("hello")
    assert "hello" in file_reader("note.txt")
    assert "not allowed" in file_reader("secret.bin")
    assert "Access denied" in file_reader("/etc/hostname.txt")


def test_datetime_tool():
    assert datetime_tool("timestamp").isdigit()
    assert "Unknown action" in datetime_tool("nope")


def test_execute_tool_unknown():
    try:
        execute_tool("nope", {})
    except KeyError as e:
        assert "Unknown tool" in str(e)
    else:
        raise AssertionError("expected KeyError")
