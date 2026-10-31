"""技能系统：Harness 的能力以"技能"为单位组合。

一个技能 = 一组工具 + 一段提示词 + 一段 UI 描述。加载技能后：
- Agent 的可用工具集由"已启用技能"决定（界面对应能力）。
- 服务化界面的仪表盘由技能动态生成（界面完全由技能组成）。
这样新增能力只需放一个技能文件，无需改代码。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["Skill", "SkillManager"]


def _load_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if path.endswith(".json"):
        return json.loads(text)
    # YAML：若可用则解析，否则提示
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except Exception:
        # 极简缩进解析兜底（仅支持本仓库技能文件这种扁平结构）
        raise RuntimeError(f"技能 {path} 需要 YAML 支持：pip install pyyaml")


@dataclass
class Skill:
    name: str
    title: str = ""
    description: str = ""
    tools: List[str] = field(default_factory=list)   # 该技能提供的工具名
    prompt: str = ""                                  # 注入给模型的领域提示
    ui: Dict[str, Any] = field(default_factory=dict)  # 界面描述（面板/字段）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title or self.name,
            "description": self.description,
            "tools": self.tools,
            "prompt": self.prompt,
            "ui": self.ui,
        }


class SkillManager:
    def __init__(self, skills_dir: Optional[str] = None):
        self.skills_dir = skills_dir or os.path.join(
            os.path.dirname(__file__), "skills"
        )
        self._skills: Dict[str, Skill] = {}
        self.load_all()

    def load_all(self) -> None:
        if not os.path.isdir(self.skills_dir):
            return
        for fname in sorted(os.listdir(self.skills_dir)):
            if fname.startswith(".") or not (
                fname.endswith(".json") or fname.endswith(".yaml") or fname.endswith(".yml")
            ):
                continue
            try:
                data = _load_file(os.path.join(self.skills_dir, fname))
                self.add(Skill(**data))
            except Exception as e:  # noqa
                print(f"[skill] 加载失败 {fname}: {e}")

    def add(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list(self) -> List[Skill]:
        return list(self._skills.values())

    def names(self) -> List[str]:
        return list(self._skills)

    def all_tool_names(self) -> List[str]:
        out: List[str] = []
        for s in self._skills.values():
            for t in s.tools:
                if t not in out:
                    out.append(t)
        return out

    def combined_prompt(self) -> str:
        return "\n\n".join(
            f"【技能 {s.title or s.name}】{s.description}\n{s.prompt}".strip()
            for s in self._skills.values()
            if s.prompt
        )
