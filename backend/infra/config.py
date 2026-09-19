# -*- coding: utf-8 -*-
"""配置层：多供应商注册表，全部走 OpenAI 兼容协议。

换供应商只需改 .env 的 LLM_PROVIDER 与对应变量，业务代码零改动。
LLM_MOCK=1 时走演示模式，无 Key 也能联调。
"""
import os
from pathlib import Path

from dotenv import load_dotenv


def _find_root(marker: str = ".env") -> Path:
    """向上查找项目根目录（含 .env 的那一层）。

    不用「__file__ 往上数 N 层」的写法：那写法一旦文件在目录间移动就会悄悄失准
    ——本项目把 config.py 从 backend/ 挪到 backend/infra/ 时真实踩过，
    表现为「Key 明明配了却说没配置」。向上找锚点文件才不会被目录调整打脸。
    """
    p = Path(__file__).resolve().parent
    for _ in range(6):
        if (p / marker).exists():
            return p
        p = p.parent
    return Path(__file__).resolve().parent.parent.parent


_ROOT = _find_root()
load_dotenv(_ROOT / ".env")

# 供应商注册表：新增供应商只需在这里加一段
PROVIDERS = {
    "zhipu": {
        "key": "ZHIPU_API_KEY",
        "model": "ZHIPU_MODEL",
        "url": "ZHIPU_API_URL",
        "default_model": "glm-4.5-flash",
        # 注意：必须用标准端点。coding 端点（/api/coding/paas/v4/）需要单独订阅
        # Coding Plan，普通 Key 会返回 429「余额不足或无可用资源包」。
        "default_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    },
    "doubao": {
        "key": "DOUBAO_API_KEY",
        "model": "DOUBAO_MODEL",
        "url": "DOUBAO_API_URL",
        "default_model": "doubao-pro-32k",
        "default_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
    },
}


def provider() -> dict:
    """当前供应商配置：{name, api_key, model, url, extra}"""
    name = (os.getenv("LLM_PROVIDER") or "zhipu").strip().lower()
    spec = PROVIDERS.get(name) or PROVIDERS["zhipu"]
    return {
        "name": name,
        "api_key": (os.getenv(spec["key"]) or "").strip(),
        "model": (os.getenv(spec["model"]) or "").strip() or spec["default_model"],
        "url": (os.getenv(spec["url"]) or "").strip() or spec["default_url"],
        "extra": provider_extra(name),
    }


def provider_extra(name: str) -> dict:
    """供应商特有的额外请求参数，以 JSON 形式配置在 .env 里。

    为什么做成配置而不是写死代码：各家供应商的扩展参数五花八门
    （豆包的 thinking、某些厂的 enable_search 等），把它们固化进代码意味着
    每支持一个新参数就要改一次 LLM 层。做成 JSON 配置后，**加参数不用改代码**。

    用法（.env）：
        DOUBAO_EXTRA={"thinking":{"type":"disabled"}}

    **为什么本项目要关掉豆包的深度思考**：实测 2.0 系列默认开启思考模式，
    延迟增加 4~6 倍（mini 档 3.78s → 0.96s，lite 档 18.5s → 3.16s），
    而我们的任务是「判断调哪个工具、填什么参数」，属于模式识别而非复杂推理，
    思考链带来的收益接近零、代价却是好几倍延迟。**能力用不上就是纯成本。**
    """
    raw = (os.getenv(f"{name.upper()}_EXTRA") or "").strip()
    if not raw:
        return {}
    try:
        import json
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except ValueError as e:
        import logging
        logging.getLogger(__name__).warning(
            "%s_EXTRA 不是合法 JSON，已忽略：%s", name.upper(), e)
        return {}


def mock() -> bool:
    return (os.getenv("LLM_MOCK") or "").strip() == "1"


def temperature() -> float:
    try:
        return float(os.getenv("LLM_TEMPERATURE") or 0.3)
    except ValueError:
        return 0.3


def timeout() -> int:
    try:
        return int(os.getenv("LLM_TIMEOUT") or 120)
    except ValueError:
        return 120


def max_tool_rounds() -> int:
    """工具调用轮次上限。

    取值权衡：太小会导致复杂问题（如「未来三天哪天适合做某事」）还没得出结论
    就被强制中断——评估集里真实出现过；太大则会让模型陷入无效循环时浪费额度。
    8 轮足以覆盖「地名解析 + 数据查询 + 计算 + 知识检索 + 多天评估」的组合，
    同时保留兜底能力。
    """
    try:
        return int(os.getenv("MAX_TOOL_ROUNDS") or 8)
    except ValueError:
        return 8
