"""Reading a JSONL log line by line, skipping what does not parse."""
import json


def lines(path):
    """Non-empty, stripped lines of a text file; nothing when it cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line
    except OSError:
        return


def parse(text_lines, *needles):
    """Each of `text_lines` that parses to a JSON object. With `needles`, only lines holding one
    of them are parsed at all: a cheap substring test that skips most of a long log."""
    for line in text_lines:
        if needles and not any(n in line for n in needles):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            yield ev


def records(path, *needles):
    """Each line of a JSONL file that parses to a JSON object (`parse`)."""
    return parse(lines(path), *needles)
