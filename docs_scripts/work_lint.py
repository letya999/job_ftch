#!/usr/bin/env python3
"""Проверка папок изменений и реестра техдолга в .work.

Запускается через just:

    just lint-work    проверить
    just fix          пересчитать состояния фаз и пересобрать доску

Состояние не хранится. Статус задачи лежит в её файле, статус фазы считается из задач,
статус плана - из фаз, а зона папки обязана соответствовать статусу плана.

Код возврата 1, если есть хотя бы одна ошибка.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("Нужен PyYAML: just setup", file=sys.stderr)
    raise SystemExit(2) from None

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".work"
ZONES = ("todo", "in-progress", "done")
DEBT = WORK / "tech-debt"

DIR_RE = re.compile(r"^CHG-\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*$")
DEBT_RE = re.compile(r"^DEBT-\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
PHASE_RE = re.compile(r"^P-\d+$")
TASK_RE = re.compile(r"^T-\d{2}$")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)

TASK_STATUSES = ("todo", "in_progress", "blocked", "done", "cancelled")

BOARD_START = "<!-- ДОСКА: генерируется через just fix, руками не править -->"
BOARD_END = "<!-- КОНЕЦ ДОСКИ -->"
PHASES_START = "<!-- ФАЗЫ: генерируется через just fix, руками не править -->"
PHASES_END = "<!-- КОНЕЦ ФАЗ -->"
DEBT_START = "<!-- СОДЕРЖИМОЕ: генерируется через just fix, руками не править -->"
DEBT_END = "<!-- КОНЕЦ СОДЕРЖИМОГО -->"


@dataclass
class Issue:
    level: str
    path: Path
    code: str
    message: str


@dataclass
class Task:
    id: str
    path: Path
    status: str
    title: str


@dataclass
class Phase:
    id: str
    title: str
    task_ids: list[str]
    tasks: list[Task] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Статус фазы выводится из её задач, а не хранится отдельным полем."""
        alive = [t for t in self.tasks if t.status != "cancelled"]
        if not alive:
            return "cancelled"
        if all(t.status == "done" for t in alive):
            return "done"
        if any(t.status == "blocked" for t in alive):
            return "blocked"
        if any(t.status in ("in_progress", "done") for t in alive):
            return "in_progress"
        return "todo"

    @property
    def progress(self) -> str:
        alive = [t for t in self.tasks if t.status != "cancelled"]
        done = sum(1 for t in alive if t.status == "done")
        return f"{done}/{len(alive)}" if alive else "-"


@dataclass
class Change:
    zone: str
    path: Path
    meta: dict
    body: str
    phases: list[Phase] = field(default_factory=list)

    @property
    def id(self) -> str:
        return "-".join(self.path.name.split("-")[:2])

    @property
    def title(self) -> str:
        m = re.search(r"^#\s+(.+)$", self.body, re.M)
        return m.group(1).strip() if m else self.path.name

    @property
    def status(self) -> str:
        """Статус плана выводится из фаз. Одна фаза - статус плана равен её статусу."""
        if not self.phases:
            return "todo"
        statuses = [p.status for p in self.phases]
        if all(s in ("done", "cancelled") for s in statuses):
            return "done"
        if "blocked" in statuses:
            return "blocked"
        if any(s in ("in_progress", "done") for s in statuses):
            return "in_progress"
        return "todo"

    @property
    def progress(self) -> str:
        tasks = [t for p in self.phases for t in p.tasks if t.status != "cancelled"]
        done = sum(1 for t in tasks if t.status == "done")
        return f"{done}/{len(tasks)}" if tasks else "-"


class WorkLinter:
    def __init__(self, fix: bool) -> None:
        self.fix = fix
        self.issues: list[Issue] = []
        self.changes: list[Change] = []

    def error(self, path: Path, code: str, message: str) -> None:
        self.issues.append(Issue("error", path, code, message))

    def warn(self, path: Path, code: str, message: str) -> None:
        self.issues.append(Issue("warning", path, code, message))

    # -- обход -----------------------------------------------------------

    def run(self) -> None:
        if not WORK.exists():
            return
        for zone in ZONES:
            zone_dir = WORK / zone
            if not zone_dir.exists():
                continue
            for path in sorted(p for p in zone_dir.iterdir() if p.is_dir()):
                self.check_change(zone, path)
        self.check_unique_ids()
        self.check_debt()
        self.sync_board()

    def read_front(self, file: Path, code: str):
        text = file.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        if not m:
            self.error(file, code, "нет frontmatter")
            return None
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as exc:
            self.error(file, "WK04", f"frontmatter не разбирается: {exc}")
            return None
        if not isinstance(meta, dict):
            self.error(file, "WK04", "frontmatter должен быть отображением")
            return None
        return meta, m.group(2)

    def check_change(self, zone: str, path: Path) -> None:
        if not DIR_RE.match(path.name):
            self.error(path, "WK01", "имя папки не по форме CHG-NNN-краткое-название")

        plan = path / "plan.md"
        if not plan.exists():
            self.error(path, "WK02", "нет plan.md")
            return
        parsed = self.read_front(plan, "WK03")
        if not parsed:
            return
        meta, body = parsed

        change = Change(zone=zone, path=path, meta=meta, body=body)
        self.check_plan_meta(plan, change)
        self.collect_phases(plan, change)
        self.check_zone(plan, change)
        self.changes.append(change)

        if self.fix:
            self.sync_phase_table(plan, change)

    # -- проверки --------------------------------------------------------

    def check_plan_meta(self, plan: Path, change: Change) -> None:
        extra = set(change.meta) - {"spec", "phases"}
        if extra:
            self.error(
                plan,
                "WK10",
                f"лишние поля: {', '.join(sorted(extra))}. Разрешены только spec и phases: "
                f"состояния выводятся из задач и зоны, хранить их нельзя",
            )

        for heading in ("## Цель", "## Критерии приёмки"):
            if heading not in change.body:
                self.error(plan, "WK11", f"нет раздела {heading}")

        spec = change.meta.get("spec")
        if spec and not (ROOT / str(spec)).exists():
            self.error(plan, "WK50", f"постановка не найдена: {spec}")

    def collect_phases(self, plan: Path, change: Change) -> None:
        phases = change.meta.get("phases")
        if not isinstance(phases, list) or not phases:
            self.error(plan, "WK12", "нет ни одной фазы в phases")
            return

        tasks_dir = change.path / "tasks"
        declared: set[str] = set()

        for raw in phases:
            if not isinstance(raw, dict):
                self.error(plan, "WK12", "фаза должна быть отображением")
                continue
            pid = str(raw.get("id", ""))
            if not PHASE_RE.match(pid):
                self.error(plan, "WK12", f"идентификатор фазы не по форме P-N: {pid}")
            ids = [str(t) for t in (raw.get("tasks") or [])]
            phase = Phase(id=pid, title=str(raw.get("title", "")), task_ids=ids)

            for tid in ids:
                if not TASK_RE.match(tid):
                    self.error(plan, "WK20", f"идентификатор задачи не по форме T-NN: {tid}")
                    continue
                if tid in declared:
                    self.error(plan, "WK20", f"задача {tid} указана в двух фазах")
                declared.add(tid)
                task = self.load_task(tasks_dir / f"{tid}.md", tid)
                if task:
                    phase.tasks.append(task)

            change.phases.append(phase)

        if tasks_dir.exists():
            for file in sorted(tasks_dir.glob("*.md")):
                if file.stem not in declared:
                    self.error(
                        file,
                        "WK23",
                        f"файл задачи не указан ни в одной фазе плана: {file.stem}",
                    )

    def load_task(self, file: Path, tid: str):
        if not file.exists():
            self.error(file, "WK20", f"нет файла задачи {tid}")
            return None
        parsed = self.read_front(file, "WK03")
        if not parsed:
            return None
        meta, body = parsed

        extra = set(meta) - {"status"}
        if extra:
            self.error(file, "WK10", f"лишние поля: {', '.join(sorted(extra))}")

        status = str(meta.get("status", ""))
        if status not in TASK_STATUSES:
            self.error(
                file,
                "WK22",
                f"статус {status} недопустим, разрешены {', '.join(TASK_STATUSES)}",
            )
            status = "todo"

        if "## Критерии приёмки" not in body:
            self.error(file, "WK21", "нет раздела с критериями приёмки")

        m = re.search(r"^#\s+(.+)$", body, re.M)
        return Task(id=tid, path=file, status=status, title=m.group(1).strip() if m else tid)

    def check_zone(self, plan: Path, change: Change) -> None:
        """Зона папки обязана соответствовать состоянию, выведенному из задач."""
        expected = {
            "todo": "todo",
            "in_progress": "in-progress",
            "blocked": "in-progress",
            "done": "done",
        }[change.status]
        if change.zone != expected:
            self.error(
                plan,
                "WK40",
                f"состояние плана {change.status}, а папка лежит в {change.zone}. "
                f"Ожидается {expected}: перенеси папку",
            )

        if change.zone == "done":
            if "{{" in change.body:
                self.error(plan, "WK41", "в done остались незаполненные плейсхолдеры")
            for phase in change.phases:
                for task in phase.tasks:
                    if "{{" in task.path.read_text(encoding="utf-8"):
                        self.error(task.path, "WK41", "в done остались плейсхолдеры")

    def check_unique_ids(self) -> None:
        seen: dict[str, Path] = {}
        for change in self.changes:
            if change.id in seen:
                self.error(
                    change.path,
                    "WK60",
                    f"идентификатор {change.id} уже занят: {seen[change.id].relative_to(ROOT)}",
                )
            seen[change.id] = change.path

    def check_debt(self) -> None:
        """Реестр техдолга живёт в .work, поэтому его таблицу собирает этот линтер."""
        if not DEBT.exists():
            return
        rows = []
        for file in sorted(DEBT.glob("*.md")):
            if file.name in ("index.md", "TEMPLATE-DEBT.md"):
                continue
            if not DEBT_RE.match(file.name):
                self.error(file, "WK80", "имя не по форме DEBT-NNN-краткое-название.md")
                continue
            text = file.read_text(encoding="utf-8")
            for heading in ("## Где", "## Чем расплачиваемся", "## Когда переделывать"):
                if heading not in text:
                    self.error(file, "WK80", f"нет раздела {heading}")
            title = re.search(r"^#\s+(.+)$", text, re.M)
            when = re.search(r"## Когда переделывать\n+(.+)", text)
            rows.append(
                f"| [{file.stem}]({file.name}) | "
                f"{title.group(1).strip() if title else file.stem} | "
                f"{(when.group(1).strip() if when else '')[:60]} |"
            )

        index = DEBT / "index.md"
        if not index.exists():
            return
        text = index.read_text(encoding="utf-8")
        bounds = self.bounds(text, DEBT_START, DEBT_END)
        if not bounds:
            self.error(index, "WK80", "в реестре нет маркеров таблицы")
            return
        block = "\n".join(
            [
                DEBT_START,
                "",
                "| Запись | О чём | Когда переделывать |",
                "| ------ | ----- | ------------------ |",
                *(rows or ["| - | записей нет | - |"]),
                "",
                DEBT_END,
            ]
        )
        a, b = bounds
        if text[a:b].strip() == block.strip():
            return
        if self.fix:
            index.write_text(text[:a] + block + text[b:], encoding="utf-8", newline="\n")
        else:
            self.error(index, "WK80", "реестр разошёлся с записями. Запусти just fix")

    # -- генерируемые блоки ----------------------------------------------

    @staticmethod
    def bounds(text: str, start: str, end: str):
        a, b = text.find(start), text.find(end)
        return (a, b + len(end)) if a != -1 and b > a else None

    def sync_phase_table(self, plan: Path, change: Change) -> None:
        text = plan.read_text(encoding="utf-8")
        bounds = self.bounds(text, PHASES_START, PHASES_END)
        if not bounds:
            return
        rows = [
            f"| {p.id} | {p.title} | {', '.join(p.task_ids) or '-'} | {p.status} | {p.progress} |"
            for p in change.phases
        ] or ["| - | фаз нет | - | - | - |"]
        block = "\n".join(
            [
                PHASES_START,
                "",
                "| Фаза | О чём | Задачи | Состояние | Готово |",
                "| ---- | ----- | ------ | --------- | ------ |",
                *rows,
                "",
                PHASES_END,
            ]
        )
        a, b = bounds
        new = text[:a] + block + text[b:]
        if new != text:
            plan.write_text(new, encoding="utf-8", newline="\n")

    def render_board(self) -> str:
        order = {"in-progress": 0, "todo": 1, "done": 2}
        rows = []
        for c in sorted(self.changes, key=lambda c: (order[c.zone], c.path.name)):
            spec = f"`{Path(str(c.meta['spec'])).stem}`" if c.meta.get("spec") else "-"
            link = c.path.relative_to(WORK).as_posix()
            rows.append(
                f"| [{c.id}]({link}/plan.md) | {c.title} | {c.zone} | {c.status} | "
                f"{c.progress} | {spec} |"
            )
        if not rows:
            rows = ["| - | сейчас работ нет | - | - | - | - |"]
        return "\n".join(
            [
                BOARD_START,
                "",
                "| CHG | Что делаем | Зона | Состояние | Готово | Спека |",
                "| --- | ---------- | ---- | --------- | ------ | ----- |",
                *rows,
                "",
                BOARD_END,
            ]
        )

    def sync_board(self) -> None:
        index = WORK / "index.md"
        if not index.exists():
            return
        text = index.read_text(encoding="utf-8")
        bounds = self.bounds(text, BOARD_START, BOARD_END)
        if not bounds:
            self.error(index, "WK70", "в index.md нет маркеров доски")
            return
        a, b = bounds
        fresh = self.render_board()
        if text[a:b].strip() == fresh.strip():
            return
        if self.fix:
            index.write_text(text[:a] + fresh + text[b:], encoding="utf-8", newline="\n")
        else:
            self.error(index, "WK70", "доска разошлась с работами. Запусти just fix")

    # -- вывод -----------------------------------------------------------

    def report(self, fmt: str) -> int:
        errors = [i for i in self.issues if i.level == "error"]
        if fmt == "github":
            for i in self.issues:
                kind = "error" if i.level == "error" else "warning"
                print(f"::{kind} file={i.path.relative_to(ROOT)},title={i.code}::{i.message}")
        else:
            for i in sorted(self.issues, key=lambda i: (str(i.path), i.code)):
                mark = "ОШИБКА " if i.level == "error" else "warning"
                print(f"{mark} {i.path.relative_to(ROOT)} [{i.code}] {i.message}")
            print()
            print(
                f"Изменений: {len(self.changes)}, ошибок: {len(errors)}, "
                f"предупреждений: {len(self.issues) - len(errors)}"
            )
        return 1 if errors else 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix", action="store_true", help="пересчитать состояния фаз и пересобрать доску"
    )
    parser.add_argument("--format", choices=["text", "github"], default="text")
    args = parser.parse_args()

    linter = WorkLinter(fix=args.fix)
    linter.run()
    return linter.report(args.format)


if __name__ == "__main__":
    sys.exit(main())
