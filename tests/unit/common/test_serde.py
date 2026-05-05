from __future__ import annotations

from dataclasses import dataclass, field

from src.common.serde import to_dict


@dataclass(slots=True, kw_only=True)
class _Inner:
    x: int
    y: str = ""


@dataclass(slots=True, kw_only=True)
class _Outer:
    name: str
    inner: _Inner
    tags: list[str] = field(default_factory=list)


class TestToDict:
    def test_flat_dataclass(self):
        d = to_dict(_Inner(x=1, y="a"))
        assert d == {"x": 1, "y": "a"}

    def test_nested_dataclass(self):
        d = to_dict(_Outer(name="o", inner=_Inner(x=2, y="b")))
        assert d["name"] == "o"
        assert d["inner"]["x"] == 2
        assert d["inner"]["y"] == "b"

    def test_list_of_dataclasses(self):
        items = [_Inner(x=1), _Inner(x=2)]
        d = to_dict(items)
        assert d == [{"x": 1, "y": ""}, {"x": 2, "y": ""}]

    def test_nested_dict(self):
        d = to_dict({"a": _Inner(x=1)})
        assert d == {"a": {"x": 1, "y": ""}}

    def test_primitive_passthrough(self):
        assert to_dict(1) == 1
        assert to_dict("hello") == "hello"
        assert to_dict(None) is None

    def test_list_of_primitives(self):
        assert to_dict([1, 2, 3]) == [1, 2, 3]
