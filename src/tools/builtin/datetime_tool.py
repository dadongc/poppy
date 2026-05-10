from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from src.common.types import ToolResult


class DateTimeTool:
    name = "datetime"
    description = "获取当前日期时间，进行时区转换和时间计算。"
    schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["now", "convert", "add", "diff"],
                "description": "now=获取当前时间, convert=时区转换, add=时间加减, diff=时间差",
            },
            "value": {
                "type": "string",
                "description": "ISO 8601 时间字符串（convert/add/diff 时必需）",
            },
            "timezone": {
                "type": "string",
                "description": "时区名称，如 Asia/Shanghai、America/New_York（convert 时必需）",
            },
            "amount": {
                "type": "integer",
                "description": "加减的数值（add 时使用）",
            },
            "unit": {
                "type": "string",
                "enum": ["seconds", "minutes", "hours", "days"],
                "description": "加减的单位（add 时使用，默认 hours）",
            },
            "target_value": {
                "type": "string",
                "description": "第二个 ISO 8601 时间字符串（diff 时使用）",
            },
        },
        "required": ["action"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        action = args["action"]

        if action == "now":
            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content=datetime.now(UTC).isoformat(),
            )

        if action == "convert":
            value = args.get("value", "")
            tz_name = args.get("timezone", "Asia/Shanghai")
            try:
                dt = datetime.fromisoformat(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                target = dt.astimezone(ZoneInfo(tz_name))
                return ToolResult(
                    call_id="",
                    name=self.name,
                    status="ok",
                    content=target.isoformat(),
                )
            except Exception as e:
                return ToolResult(
                    call_id="", name=self.name, status="error", error_message=str(e)
                )

        if action == "add":
            value = args.get("value", "")
            amount = args.get("amount", 0)
            unit = args.get("unit", "hours")
            try:
                dt = datetime.fromisoformat(value)
                kwargs = {unit: amount}
                result = dt + timedelta(**kwargs)
                return ToolResult(
                    call_id="",
                    name=self.name,
                    status="ok",
                    content=result.isoformat(),
                )
            except Exception as e:
                return ToolResult(
                    call_id="", name=self.name, status="error", error_message=str(e)
                )

        if action == "diff":
            value = args.get("value", "")
            target = args.get("target_value", "")
            try:
                dt1 = datetime.fromisoformat(value)
                dt2 = datetime.fromisoformat(target)
                delta = dt2 - dt1
                total_seconds = int(delta.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                content = f"{total_seconds}s ({hours}h {minutes}m {seconds}s)"
                return ToolResult(
                    call_id="",
                    name=self.name,
                    status="ok",
                    content=content,
                )
            except Exception as e:
                return ToolResult(
                    call_id="", name=self.name, status="error", error_message=str(e)
                )

        return ToolResult(
            call_id="",
            name=self.name,
            status="error",
            error_message=f"unknown action: {action}",
        )
