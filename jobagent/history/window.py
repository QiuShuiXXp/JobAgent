
from dataclasses import dataclass

from jobagent.config.settings import settings
from jobagent.domain.messages import Turn
from jobagent.history.builder import ChatHistoryBuilder

"""
工作记忆：滑动窗口（最近 N 轮原文）。

两层裁剪：先按轮数取尾部，再按字符预算从最旧的开始丢——新消息与当前轮
相关性最高，超预算时先牺牲旧的。

"""
@dataclass(frozen=True, slots=True)
class HistoryWindow:
    max_turns: int = 3
    max_chars: int = 4000

    @classmethod
    def from_settings(cls) -> "HistoryWindow":
        return cls(max_turns=settings.history_window_turns,
                   max_chars=settings.history_window_chars)

    def select(self, turns: list[Turn]) -> list[Turn]:
        """返回不超过预算的尾部轮次（保持原顺序）。"""
        budget = self.max_chars
        kept: list[Turn] = []
        tail = turns[-self.max_turns:] if self.max_turns > 0 else []
        for turn in reversed(tail):
            cost = len(ChatHistoryBuilder.build([turn]))
            if cost > budget:
                break
            kept.append(turn)
            budget -= cost
        kept.reverse()
        return kept
