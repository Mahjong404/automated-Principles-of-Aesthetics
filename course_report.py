import re
import sys
from dataclasses import dataclass, field


def safe_text(value):
    value = re.sub(r"(?:https?|wss?)://[^\s]+", "[链接已省略]", str(value), flags=re.I)
    value = re.sub(
        r"(?i)\b(api[_ -]?key|password|token|authorization|bearer)\s*[:= ]\s*(?:bearer\s+)?[^\s，；]+",
        r"\1=[已省略]", value,
    )
    value = re.sub(r"\b[A-Za-z0-9_+/=.-]{32,}\b|\b1[3-9]\d{9}\b", "[已省略]", value)
    return " ".join(value.split())[:240]


@dataclass
class RunReport:
    chapter: str = "尚未定位章节"
    stage: str = "启动"
    next_step: str = "重新运行脚本"
    completed: list[str] = field(default_factory=list)

    def select(self, chapter):
        self.chapter = safe_text(chapter)

    def enter(self, stage, next_step):
        self.stage = stage
        self.next_step = next_step

    def note(self, action):
        self.completed.append(f"{self.chapter} · {action}")

    def print_receipt(self, result, failed=False):
        stream = sys.stderr if failed else sys.stdout
        print("\n本次运行回执", file=stream)
        for action in self.completed:
            print(f"  {action}", file=stream)
        if not self.completed:
            print("  本次没有确认完成的任务", file=stream)
        print(f"  停在 {self.chapter} · {self.stage}", file=stream)
        print(f"  结果 {safe_text(result)}", file=stream)
        print(f"  下次 {safe_text(self.next_step)}", file=stream)
        if failed:
            print(f"故障诊断摘要 | 章节={self.chapter} | 阶段={self.stage} | 原因={safe_text(result)}", file=stream)
