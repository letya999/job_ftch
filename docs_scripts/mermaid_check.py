#!/usr/bin/env python3
"""Рендер всех блоков mermaid через mmdc, чтобы поймать ошибки синтаксиса.

Запуск:
    python tools/mermaid_check.py

Требует установленного @mermaid-js/mermaid-cli. Блоки с незаполненными
плейсхолдерами шаблона пропускаются: в них ещё нечего рендерить.

docs_lint.py проверяет тип диаграммы и баланс скобок, чего достаточно локально.
Содержимое блока разбирает только сам mermaid, поэтому полная проверка живёт
отдельно и запускается на раннере.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCK_RE = re.compile(r"^```mermaid\n(.*?)^```", re.S | re.M)
SKIP_DIRS = {".git", "site", "node_modules", ".venv"}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    failures = 0
    checked = 0

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.svg"
        for path in sorted(ROOT.rglob("*.md")):
            if SKIP_DIRS & set(path.relative_to(ROOT).parts):
                continue
            text = path.read_text(encoding="utf-8")
            for index, block in enumerate(BLOCK_RE.findall(text), start=1):
                if "{{" in block:
                    continue
                src = Path(tmp) / f"block{index}.mmd"
                src.write_text(block, encoding="utf-8")
                result = subprocess.run(
                    ["mmdc", "-i", str(src), "-o", str(out)],
                    capture_output=True, text=True,
                )
                checked += 1
                if result.returncode != 0:
                    failures += 1
                    detail = (result.stderr or result.stdout).strip().splitlines()
                    reason = detail[-1] if detail else "неизвестная ошибка"
                    rel = path.relative_to(ROOT)
                    print(f"::error file={rel},title=mermaid::блок {index}: {reason}")
                    print(f"ОШИБКА {rel}: блок {index} не рендерится: {reason}")

    print(f"Проверено блоков: {checked}, не отрендерилось: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
