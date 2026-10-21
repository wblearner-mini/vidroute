import math

import pytest

from vidroute.core.util.yamlish import YamlError, loads

yaml = pytest.importorskip("yaml")

# Documents inside the supported subset must parse exactly like PyYAML.
AGREE = [
    "a: 1\nb: two\nc: 3.5\nd: true\ne: null\nf: ~\ng:\n",
    "list:\n  - a\n  - b\n  - 3\n",
    "list:\n- a\n- b\nnext: 1\n",
    "- id: x\n  name: y\n- id: z\n  tags: [a, 'b', \"c\"]\n",
    "- - a\n  - b\n- - c\n",
    "outer:\n  inner:\n    deep: [1, 2, {k: v, n: [x]}]\n  sib: 'it''s'\n",
    "url: https://example.com/a?b=c#frag\nport: 8080\n",
    "msg: \"tab\\tnew\\nline \\u4e2d\"\n",
    "# leading comment\n---\nkey: value # trailing comment\nother: 'a # not comment'\n",
    "text: |\n  line one\n  line two\nnext: 1\n",
    "text: |-\n  stripped\n\nnext: 2\n",
    "text: >\n  folded\n  together\n\n  para\nnext: 3\n",
    "- |\n  item block\n- plain\n",
    "empty_list: []\nempty_map: {}\nneg: -5\nhex: 0x1f\nfloat: -0.25\n",
    "unicode: 中文 名字\nquoted_key: 1\n\"spaced key\": ok\n",
    "nested:\n  - name: a\n    items:\n      - 1\n      - 2\n  - name: b\n    items: []\n",
    "x: 'single'\ny: \"double\"\nz: plain text with spaces\n",
    "glob: \"qwen3.8-omni-flash*\"\nstar: '*'\n",
    "inf: .inf\nninf: -.inf\n",
]


@pytest.mark.parametrize("doc", AGREE)
def test_matches_pyyaml(doc):
    assert loads(doc) == yaml.safe_load(doc)


def test_nan():
    assert math.isnan(loads("v: .nan")["v"])


def test_yaml12_octal():
    # YAML 1.2 octal; PyYAML (1.1) would keep "0o17" as a string.
    assert loads("oct: 0o17") == {"oct": 15}


def test_yaml12_booleans_stay_strings():
    # YAML 1.1 (PyYAML) turns these into booleans; we deliberately do not.
    assert loads("a: yes\nb: no\nc: on\nd: off") == {"a": "yes", "b": "no", "c": "on", "d": "off"}


@pytest.mark.parametrize(
    "doc",
    [
        "a: &anchor 1\nb: *anchor\n",
        "a: !!str 1\n",
        "a: 1\na: 2\n",
        "a:\n\tb: 1\n",
        "a: [1, 2\n",
        "a: 'unterminated\n",
        "a: b: c\n",
        "a:\n  word1\n  word2\n",
        "a: 1\n---\nb: 2\n",
        "%YAML 1.2\n---\na: 1\n",
        "a:\n  - x\n  y: 1\n",
    ],
)
def test_rejects_unsupported(doc):
    with pytest.raises(YamlError):
        loads(doc)


def test_error_has_line_number():
    with pytest.raises(YamlError) as exc:
        loads("a: 1\nb: 2\nb: 3\n", source="f.yaml")
    assert "f.yaml:3" in str(exc.value)


def test_empty_document():
    assert loads("") is None
    assert loads("# only comments\n\n") is None


def test_bom_and_crlf():
    assert loads("\ufeffa: 1\r\nb: [x, y]\r\n") == {"a": 1, "b": ["x", "y"]}
