# -*- coding: utf-8 -*-
"""会话存储：内存实现，带 TTL 与容量淘汰。

**核心设计：会话历史分两份存**

| 用途 | 内容 | 为什么 |
|---|---|---|
| `history`（给模型看） | 只保留「问题 + 最终回答」，丢弃工具调用的中间过程 | 一次逐小时气象数据就有三千字符，几轮下来上下文必爆 |
| `records`（给用户看） | 完整保留工具调用、参数、返回、校验报告 | 前端要回放过程、要展示轨迹 |

如果不区分：要么模型上下文爆掉，要么用户看不到执行过程。

**为什么抽象成类而不是直接用 dict**
将来换 Redis 只需新写一个实现了同样方法的类，调用方一行不改。
本项目单机部署用内存实现足够，但接口要留好——这是分层设计的意义。
"""
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

TTL_SECONDS = 2 * 3600      # 2 小时未活动即过期
MAX_SESSIONS = 200          # 容量上限，超出按最久未使用淘汰
MAX_HISTORY_TURNS = 6       # 给模型看的历史最多保留 6 轮，防止上下文膨胀


@dataclass
class Session:
    id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list[dict] = field(default_factory=list)   # 给模型：精简
    records: list[dict] = field(default_factory=list)   # 给用户：完整

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turns": len(self.records),
            "records": self.records,
        }


class SessionStore:
    def __init__(self, ttl: int = TTL_SECONDS, max_sessions: int = MAX_SESSIONS):
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl
        self._max = max_sessions
        # FastAPI 的同步路由跑在线程池里，加锁防止并发读写把 dict 搞坏
        self._lock = threading.Lock()

    # ---------- 基础操作 ----------
    def create(self) -> Session:
        s = Session(id=uuid.uuid4().hex[:16])
        with self._lock:
            self._evict_locked()
            self._sessions[s.id] = s
        return s

    def get(self, session_id: str | None) -> Session:
        """取会话；不存在或已过期则新建。"""
        if session_id:
            with self._lock:
                s = self._sessions.get(session_id)
                if s and time.time() - s.updated_at <= self._ttl:
                    return s
            log.info("会话 %s 不存在或已过期，新建会话", session_id)
        return self.create()

    def append_turn(self, session: Session, question: str, answer: str,
                    trace: list[dict] | None = None,
                    verification: dict | None = None, mode: str = "single") -> None:
        with self._lock:
            session.updated_at = time.time()
            # 给用户看的：完整记录
            session.records.append({
                "question": question,
                "answer": answer,
                "trace": trace or [],
                "verification": verification,
                "mode": mode,
                "at": time.time(),
            })
            # 给模型看的：只有问答，不带工具过程
            session.history.append({"role": "user", "content": question})
            session.history.append({"role": "assistant", "content": answer})
            # 只保留最近 N 轮
            keep = MAX_HISTORY_TURNS * 2
            if len(session.history) > keep:
                session.history = session.history[-keep:]

    def clear(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    # ---------- 淘汰 ----------
    def _evict_locked(self) -> None:
        now = time.time()
        expired = [k for k, v in self._sessions.items() if now - v.updated_at > self._ttl]
        for k in expired:
            self._sessions.pop(k, None)
        if len(self._sessions) >= self._max:
            # 按最后活动时间排序，淘汰最久未用的
            for k, _ in sorted(self._sessions.items(), key=lambda kv: kv[1].updated_at)[
                    : len(self._sessions) - self._max + 1]:
                self._sessions.pop(k, None)

    @property
    def stats(self) -> dict:
        with self._lock:
            return {"sessions": len(self._sessions), "ttl_seconds": self._ttl,
                    "max_sessions": self._max}


# 全局单例
_store = SessionStore()


def get_store() -> SessionStore:
    return _store
