from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        d: dict[str, Any] = {}
        for field_name, field_value in asdict(obj).items():  # type: ignore[arg-type]
            d[field_name] = to_dict(field_value)
        return d
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj
