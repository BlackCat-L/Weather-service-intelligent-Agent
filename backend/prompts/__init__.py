# -*- coding: utf-8 -*-
"""提示词包：与代码分离，便于单独迭代。

提示词是这个项目里改动最频繁的部分（比代码频繁得多），单独成目录的好处是
迭代提示词时不会碰到业务代码，review 时也一眼能看出「这次改的是提示词还是逻辑」。
"""
from .system import SYSTEM_PROMPT, system_prompt, with_today

__all__ = ["SYSTEM_PROMPT", "system_prompt", "with_today"]
