#!/usr/bin/env python3
"""Разворачивает шаблон в рабочие файлы проекта.

Запускается через just:

    just init

Файлы с суффиксом `.template` становятся рабочими, а те, что описывали устройство самого
шаблона, удаляются. Шаг делается один раз и вручную: автоматически при клоне его выполнить
нельзя, а забыть - легко, поэтому он вынесен в отдельную команду.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Заготовка -> рабочее имя. Файл справа перед этим удаляется: он описывал шаблон,
# а не проект, и в развёрнутом репозитории только путал бы.
RENAMES = {
    "AGENTS.template.md": "AGENTS.md",
    "CLAUDE.template.md": "CLAUDE.md",
    "README.template.md": "README.md",
    "QUICKSTART.template.md": "QUICKSTART.md",
}

# Артефакты самого шаблона, которых в проекте быть не должно.
DROP = [
    "docs/adr/ADR-0001-record-architecture-decisions.md",
]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    missing = [src for src in RENAMES if not (ROOT / src).exists()]
    if missing:
        print("Заготовки не найдены:", ", ".join(missing))
        print("Похоже, шаблон уже развёрнут. Команда ничего не делает.")
        return 1

    for src, dst in RENAMES.items():
        target = ROOT / dst
        if target.exists():
            target.unlink()
        shutil.move(ROOT / src, target)
        print(f"{src} -> {dst}")

    for path in DROP:
        target = ROOT / path
        if target.exists():
            target.unlink()
            print(f"удалено: {path}")

    print()
    print("Дальше: заполни AGENTS.md, затем `just fix` и `just check`.")
    print("Найти незаполненные места: grep -rl '{{' --include='*.md' .")
    return 0


if __name__ == "__main__":
    sys.exit(main())
