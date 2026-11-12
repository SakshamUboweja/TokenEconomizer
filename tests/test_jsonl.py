"""trace.jsonl: the one JSONL reader every session-log parser goes through."""
from tokeneconomizer.trace import jsonl


def test_parse_keeps_objects_and_skips_the_rest():
    lines = ['{"a": 1}', "not json", "", "[1, 2]", '"text"', '{"b": 2}']
    assert list(jsonl.parse(lines)) == [{"a": 1}, {"b": 2}]


def test_needles_skip_lines_that_hold_none_of_them():
    lines = ['{"type": "usage", "n": 1}', '{"type": "other"}', '{"type": "meta"}']
    assert [e["type"] for e in jsonl.parse(lines, '"usage"', '"meta"')] == ["usage", "meta"]


def test_records_reads_a_file_and_an_unreadable_one_is_empty(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"a": 1}\n\n  {"b": 2}  \nbroken\n', encoding="utf-8")
    assert list(jsonl.records(str(path))) == [{"a": 1}, {"b": 2}]
    assert list(jsonl.records(str(tmp_path / "missing.jsonl"))) == []
