# 项目编码规约

## 架构
- 三层架构: gateway / service / infra，不准跨层调用
- 业务逻辑只在 service，gateway 不写 if/else 业务判断
- infra 只做数据访问，不含业务
- gateway 只做：参数校验、鉴权、序列化、调 service

## Service
- 构造函数用 keyword-only 参数：`def __init__(self, *, dep1: Foo, dep2: Bar)`
- IO/建表 统一放 `async init()`，`__init__` 只做字段赋值
- 依赖通过构造函数注入，不用全局变量或 import 单例

## Tool
- builtin 工具放 `src/tools/builtin/`，启动时自动加载
- custom 工具放 `src/tools/custom/`，不自动加载，由 Skill 按需触发
- 每个 Tool 必须定义 `name`、`description`、`schema`、`is_builtin`、`cacheable`、`cache_ttl`

## 异常
- 所有自定义异常继承 `AgentError`（`src/common/errors.py`）
- 异常必须带 context：`raise XxxError("为什么失败") from original_error`
- 非关键路径失败可静默（如 recall 失败返回空），但必须 catch 明确异常类

## 异步
- 全部使用 `asyncio`，不引入 threading
- 后台 Task 统一由 `Runtime._workers` 管理生命周期
- 取消信号通过 `asyncio.Event` 传播，`asyncio.CancelledError` 必须显式重抛
- 并发限制用 `asyncio.Semaphore`，超时用 `asyncio.wait_for`

## 复用
- 新增 Tool 前，先在 `src/tools/builtin/` 和 `src/tools/custom/` 搜同名能力
- 重复 3 次以上的逻辑必须抽函数/工具
- 同类型常量统一放 `src/common/types.py`

## 命名
- 用户标识统一叫 `user_id`（不用 uid / userName）
- 时间字段统一 `xxx_at`（`created_at` / `updated_at` / `finished_at`）
- 文件/模块 `snake_case`，类 `PascalCase`，函数/变量 `snake_case`
- 私有成员 `_` 前缀，模块级常量 `UPPER_CASE`

## 数据
- 手写 SQL，不用 ORM
- 配置模型用 Pydantic `BaseModel`，不用 dataclass
- 业务模型用 `@dataclass(slots=True, kw_only=True)`
- Union 类型用 `X | None`，不用 `Optional[X]`
- 仅用于注解的交叉引用放 `if TYPE_CHECKING:` 块

## 禁止
- 禁止新增依赖未经讨论（检查 `pyproject.toml`）
- 禁止 `raise Exception("xxx")`，统一抛 `AgentError` 子类
- 禁止 `__init__` 做 IO/建表
- 禁止全局可变状态（配置除外）
- 禁止 `unittest.mock`，测试用手写 Stub/Fake 类
- 禁止 Protocol 显式继承，实现类用 duck typing
- 禁止 ORM

## 文档
- feature 变更必须同步更新 `docs/` 下对应编号的文档（00-90）
- 不改对应的文档 = feature 没做完

## 必须
- 每个新函数/类附单元测试（`tests/unit/` 镜像目录）
- 复杂逻辑加中文 docstring
- 所有 `.py` 文件首行 `from __future__ import annotations`
- 函数返回值必须标注类型
- 公开 API 的 docstring 写中文
