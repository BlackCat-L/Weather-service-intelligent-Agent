# -*- coding: utf-8 -*-
"""工具注册表：统一管理工具的 schema 与执行入口。

两个关键设计：

1. **参数 schema 显式声明，而非从函数签名自动推断。**
   模型是「读参数描述」来决定怎么填参数的——描述写得好不好直接决定工具调用
   准确率。自动推断出的类型信息没法表达「日期要用 YYYY-MM-DD 格式」这类
   业务约定，所以宁可多写几行，也要把描述写清楚。

2. **工具执行失败不抛异常，而是把错误当作结果返回给模型。**
   参数错了就把「可接受的参数列表」告诉它，让它自己改；接口超时就告诉它超时。
   这是 Agent 能自我纠错、而不是一报错就整通对话中断的关键。
"""
import logging

log = logging.getLogger(__name__)


class Tool:
    def __init__(self, name, description, parameters, fn):
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        self.fn = fn

    def schema(self) -> dict:
        """转成 OpenAI 兼容协议要求的 JSON Schema 形式。"""
        props, required = {}, []
        for pname, spec in self.parameters.items():
            prop = {}
            for k in ("type", "description", "enum"):
                if k in spec:
                    prop[k] = spec[k]
            if "default" in spec:
                prop["default"] = spec["default"]
            props[pname] = prop
            if spec.get("required"):
                required.append(pname)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }

    def run(self, args: dict) -> dict:
        if not isinstance(args, dict):
            args = {}
        # 只传工具真正接受的参数，多余的忽略（模型偶尔会自作主张加字段）
        known = {k: v for k, v in args.items() if k in self.parameters}
        try:
            result = self.fn(**known)
            return result if isinstance(result, dict) else {"result": result}
        except TypeError as e:
            # 参数不匹配：把可用参数告诉模型，让它自己修正后重试
            return {
                "error": f"参数错误：{e}",
                "accepts": {k: v.get("description", v.get("type", "")) for k, v in self.parameters.items()},
                "hint": "请按 accepts 里的参数说明重新调用",
            }
        except Exception as e:
            log.warning("工具 %s 执行失败：%s", self.name, e)
            return {
                "error": f"{type(e).__name__}: {e}",
                "hint": "数据获取失败，可以尝试换一个地名或缩小查询范围后重试",
            }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def tool(self, name=None, description="", parameters=None):
        """装饰器：把一个函数注册为 Agent 可调用的工具。"""
        def deco(fn):
            tname = name or fn.__name__
            self._tools[tname] = Tool(
                tname, description or (fn.__doc__ or "").strip(), parameters, fn)
            return fn
        return deco

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def call(self, name: str, args: dict) -> dict:
        t = self._tools.get(name)
        if not t:
            return {"error": f"未知工具 {name}", "available": list(self._tools.keys())}
        return t.run(args)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def subset(self, names: list[str]) -> "ToolRegistry":
        """派生一个只含指定工具的注册表——供多智能体按角色分配工具用。

        为什么用「白名单」而不是「黑名单」：新增工具时白名单方案下已有角色
        不受影响（新工具默认不分配给任何人），黑名单方案下则会被动获得新工具，
        可能引入意料之外的行为。白名单是更安全的默认。
        """
        sub = ToolRegistry()
        for n in names:
            if n in self._tools:
                sub._tools[n] = self._tools[n]
        return sub

    def __len__(self):
        return len(self._tools)
