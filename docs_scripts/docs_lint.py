#!/usr/bin/env python3
"""Проверка документации: frontmatter, ссылки, индексы, mermaid, свежесть, язык.

Запускается через just, а не напрямую:

    just lint-docs      проверить
    just fix            пересобрать таблицы в index.md

Флаги: --template разрешает плейсхолдеры {{...}}, --fix перегенерирует индексы,
--max-age задаёт порог протухания, --format github выводит аннотации для раннера.

Код возврата 1, если есть хотя бы одна ошибка. Предупреждения не влияют на код возврата.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Директории с документацией, которую проверяем.
DOC_ROOTS = ["docs", "specs", ".work"]

# Структуру .work проверяет docs_scripts/work_lint.py: там зоны, папки изменений и
# артефакты субагентов, к которым правила индексов и frontmatter неприменимы.
# Здесь у .work проверяется только целостность ссылок и разметка.
STRUCTURE_EXEMPT = [".work"]

# Файлы в корне: проверяем ссылки, разметку и язык, но не frontmatter.
# Заготовки с суффиксом .template проверяются наравне с рабочими: разворачивать
# шаблон с битыми ссылками внутри нечестно.
ROOT_DOCS = [
    "README.md", "AGENTS.md", "CLAUDE.md", "SECURITY.md", "QUICKSTART.md",
    "README.template.md", "AGENTS.template.md", "CLAUDE.template.md",
    "QUICKSTART.template.md",
]

REQUIRED_FRONTMATTER = {"title", "description", "updated"}

# Индекс обязателен, если документов в папке больше этого числа или есть вложенные папки.
INDEX_THRESHOLD = 4

MAX_DESCRIPTION_LEN = 200

MERMAID_TYPES = {
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram",
    "stateDiagram-v2", "erDiagram", "journey", "gantt", "pie", "mindmap",
    "timeline", "gitGraph", "quadrantChart", "C4Context", "C4Container",
    "C4Component", "C4Dynamic", "sankey-beta", "block-beta", "xychart-beta",
}

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+?)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[ \t]*([^\s`]*)[ \t]*$")
PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s")

# Таблица детей в index.md собирается автоматически между этими маркерами.
TABLE_START = "<!-- СОДЕРЖИМОЕ: генерируется через just fix, руками не править -->"
TABLE_END = "<!-- КОНЕЦ СОДЕРЖИМОГО -->"

# Документация ведётся на русском. Считаются слова, а не буквы: имя собственное
# вроде Conventional Commits перевешивает короткую фразу по буквам, но остаётся
# двумя словами из восьми. Код и ссылки вырезаются: пути, команды и
# идентификаторы латинские по определению.
MIN_CYRILLIC_RATIO = 0.5
MIN_WORDS = 8
CODE_SPAN_RE = re.compile(r"`[^`]*`")
MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\([^)]*\)")
CYRILLIC_WORD_RE = re.compile(r"[а-яёА-ЯЁ]{2,}")
LATIN_WORD_RE = re.compile(r"[a-zA-Z]{2,}")


@dataclass
class Issue:
    level: str  # error | warning
    path: Path
    line: int
    code: str
    message: str


class Linter:
    def __init__(self, template_mode: bool, max_age: int, fix: bool = False) -> None:
        self.template_mode = template_mode
        self.max_age = max_age
        self.fix = fix
        self.issues: list[Issue] = []
        self.descriptions: dict[Path, str] = {}

    # -- инфраструктура --------------------------------------------------

    def error(self, path: Path, line: int, code: str, message: str) -> None:
        self.issues.append(Issue("error", path, line, code, message))

    def warn(self, path: Path, line: int, code: str, message: str) -> None:
        self.issues.append(Issue("warning", path, line, code, message))

    def collect_files(self) -> list[Path]:
        files: list[Path] = []
        for name in ROOT_DOCS:
            p = ROOT / name
            if p.exists():
                files.append(p)
        for root in DOC_ROOTS:
            base = ROOT / root
            if base.exists():
                files.extend(sorted(base.rglob("*.md")))
        return files

    def needs_frontmatter(self, path: Path) -> bool:
        """Frontmatter несут документы, а не рабочие артефакты.

        Файл работы в .work/ живёт неделю и удаляется: описывать его для индекса
        незачем. Индексы этих папок - другое дело, они долгоживущие.
        """
        if path.parent == ROOT:
            return False
        rel = path.relative_to(ROOT)
        if rel.parts[0] in STRUCTURE_EXEMPT:
            return path.name == "index.md" and len(rel.parts) == 2
        return rel.parts[0] in ("docs", "specs") or path.name in ("index.md", "README.md")

    # -- проходы ---------------------------------------------------------

    def run(self) -> int:
        files = self.collect_files()

        # Первый проход: frontmatter и сбор описаний для сверки с индексами.
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.check_frontmatter(path, text)

        # Таблицы пересобираются до проверки ссылок: иначе после удаления
        # документа первый прогон ругается на ссылку в устаревшей таблице,
        # которую сам же и собирается починить, и требует второго запуска.
        if self.fix:
            self.sync_index_tables()

        # Второй проход: всё остальное, уже зная описания всех файлов.
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.check_links(path, text)
            self.check_index_annotations(path, text)
            self.check_mermaid(path, text)
            self.check_state_leak(path, text)
            self.check_language(path, text)
            self.check_index_form(path, text)
            if not self.template_mode:
                self.check_placeholders(path, text)

        self.check_indexes()
        if not self.fix:
            self.sync_index_tables()
        return 0

    def check_frontmatter(self, path: Path, text: str) -> None:
        if not self.needs_frontmatter(path):
            return

        m = FRONTMATTER_RE.match(text)
        if not m:
            self.error(path, 1, "FM001", "нет frontmatter с title, description и updated")
            return

        # Разбор настоящим парсером. Построчное чтение прощает двоеточие внутри
        # значения и фигурные скобки плейсхолдера, а любой инструмент, читающий
        # frontmatter всерьёз, на них падает.
        try:
            fields = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as exc:
            self.error(
                path, 2, "FM007",
                f"frontmatter не разбирается как YAML: {str(exc).splitlines()[0]}. "
                f"Значения со спецсимволами берутся в кавычки",
            )
            return
        if not isinstance(fields, dict):
            self.error(path, 2, "FM007", "frontmatter должен быть отображением")
            return

        extra = set(fields) - REQUIRED_FRONTMATTER
        missing = REQUIRED_FRONTMATTER - set(fields)
        if missing:
            self.error(path, 2, "FM003", f"нет обязательных полей: {', '.join(sorted(missing))}")
        if extra:
            self.error(
                path, 2, "FM004",
                f"лишние поля: {', '.join(sorted(extra))}. "
                f"Разрешены только title, description и updated",
            )

        description = str(fields.get("description", ""))
        if description:
            self.descriptions[path] = description
            if len(description) > MAX_DESCRIPTION_LEN:
                self.warn(
                    path, 2, "FM005",
                    f"description длиной {len(description)} символов, порог {MAX_DESCRIPTION_LEN}",
                )
            if not description.endswith("."):
                self.warn(path, 2, "FM006", "description без точки в конце")

        self.check_freshness(path, str(fields.get("updated", "")))

    def check_freshness(self, path: Path, value: str) -> None:
        if not value:
            return
        if PLACEHOLDER_RE.search(value):
            if not self.template_mode:
                self.error(path, 3, "FR001", "updated не заполнен")
            return
        try:
            verified = dt.date.fromisoformat(value.strip())
        except ValueError:
            self.error(path, 3, "FR002", f"updated не является датой ГГГГ-ММ-ДД: {value}")
            return
        age = (dt.date.today() - verified).days
        if age < 0:
            self.error(path, 3, "FR003", f"updated в будущем: {value}")
        elif age > self.max_age:
            self.warn(
                path, 3, "FR004",
                f"не проверялся {age} дней, порог {self.max_age}. Сверь с кодом и обнови дату",
            )

    def check_links(self, path: Path, text: str) -> None:
        for line_no, line in iter_lines_outside_fences(text):
            for match in LINK_RE.finditer(line):
                target = match.group(2)
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                if PLACEHOLDER_RE.search(target) or "*" in target:
                    continue

                anchor = ""
                if "#" in target:
                    target, anchor = target.split("#", 1)
                if not target:
                    continue

                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    self.error(
                        path, line_no, "LN001",
                        f"ссылка ведёт в никуда: {match.group(2)}",
                    )
                    continue

                if anchor and resolved.suffix == ".md":
                    self.check_anchor(path, line_no, resolved, anchor, match.group(2))

    def check_index_annotations(self, path: Path, text: str) -> None:
        """Ссылка на документ, поданная пунктом списка, обязана быть аннотирована.

        Правило действует во всех документах, а не только в индексах: список ссылок -
        это меню, и агент выбирает по нему, что открыть. Ссылки внутри связного текста
        и в таблицах не проверяются: там смысл задаёт окружающая фраза или колонка.
        """
        for line_no, item in iter_list_items(text):
            match = LINK_RE.search(item)
            if not match:
                continue
            target = match.group(2).split("#")[0]
            if target.startswith(("http://", "https://", "mailto:")) or not target:
                continue
            if PLACEHOLDER_RE.search(target) or "*" in target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists() or resolved.suffix != ".md":
                continue
            self.check_annotation(path, line_no, item, match, resolved)

    def check_anchor(self, path: Path, line_no: int, target: Path, anchor: str, raw: str) -> None:
        text = target.read_text(encoding="utf-8")
        slugs = {slugify(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.M)}
        if anchor.lower() not in slugs:
            self.warn(path, line_no, "LN002", f"якорь не найден в целевом файле: {raw}")

    def check_annotation(self, path: Path, line_no: int, item: str,
                         match: re.Match[str], target: Path) -> None:
        """Ссылка сопровождается описанием того, что внутри и когда это читать."""
        tail = item[match.end():]
        if not tail.startswith(":"):
            self.error(
                path, line_no, "AN001",
                f"ссылка в индексе без аннотации: {match.group(2)}. "
                f"Формат: `[путь](относительный): описание. Читать когда ...`",
            )
            return

        annotation = tail[1:].strip()
        if len(annotation) < 10:
            self.error(path, line_no, "AN002", f"аннотация слишком короткая: {annotation!r}")
            return

        expected = self.descriptions.get(target)
        if expected and not annotations_agree(annotation, expected):
            self.warn(
                path, line_no, "AN003",
                f"аннотация разошлась с description целевого файла.\n"
                f"    в индексе: {annotation[:80]}\n"
                f"    в файле:   {expected[:80]}",
            )

    def check_language(self, path: Path, text: str) -> None:
        """Документация ведётся на русском.

        Считается только связный текст: код, ссылки и разметка вырезаются, потому что
        пути, команды и идентификаторы латинские по определению. Строка, где после
        вырезания осталась почти одна латиница, почти наверняка забытый английский
        абзац или машинный перевод наполовину.
        """
        # Frontmatter пропускаем: там служебные поля, а не текст для читателя.
        m = FRONTMATTER_RE.match(text)
        skip_until = text[:m.end()].count("\n") if m else 0

        for line_no, line in iter_lines_outside_fences(text):
            if line_no <= skip_until:
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith(("|", ">", "<!--")):
                continue
            # Ссылка целиком вырезается вместе с текстом: в нём обычно путь.
            prose = MD_LINK_RE.sub(" ", CODE_SPAN_RE.sub(" ", stripped))
            cyr = len(CYRILLIC_WORD_RE.findall(prose))
            lat = len(LATIN_WORD_RE.findall(prose))
            if cyr + lat < MIN_WORDS:
                continue
            if cyr / (cyr + lat) < MIN_CYRILLIC_RATIO:
                self.error(
                    path, line_no, "RU001",
                    "строка не на русском. Документация ведётся на русском, "
                    "латиница остаётся только в путях, командах и идентификаторах",
                )

    # -- генерация таблиц содержимого ------------------------------------

    def index_rows(self, index: Path) -> list[str]:
        """Строки таблицы детей: имя, описание и дата сверки из их frontmatter."""
        rows = []
        children: list[tuple[str, Path]] = []
        for sub in sorted(d for d in index.parent.iterdir() if d.is_dir()):
            child_index = sub / "index.md"
            if child_index.exists():
                children.append((f"{sub.name}/", child_index))
        for doc in sorted(index.parent.glob("*.md")):
            if doc.name != "index.md":
                children.append((doc.name, doc))

        for label, target in children:
            meta = read_frontmatter(target)
            rel = target.relative_to(index.parent).as_posix()
            rows.append(
                f"| [{label}]({rel}) | {meta.get('description', '')} | "
                f"{meta.get('updated', '')} |"
            )
        return rows

    def render_table(self, index: Path) -> str:
        rows = self.index_rows(index) or ["| - | пока пусто | - |"]
        return "\n".join([
            TABLE_START, "",
            "| Документ | О чём | Сверено |",
            "| -------- | ----- | ------- |",
            *rows, "",
            TABLE_END,
        ])

    def sync_index_tables(self) -> None:
        """Таблица содержимого собирается из frontmatter детей, а не пишется руками.

        Рукописный перечень расходится с папкой на первом же добавленном файле, и
        дальше индекс уверенно врёт о том, что внутри.
        """
        for root in DOC_ROOTS:
            # .work ведёт свою доску вместо перечня документов: там папки работ,
            # а не документы, и их состояние собирает work_lint.
            if root in STRUCTURE_EXEMPT:
                continue
            base = ROOT / root
            if not base.exists():
                continue
            for index in [base / "index.md", *base.rglob("index.md")]:
                if not index.exists():
                    continue
                text = index.read_text(encoding="utf-8")
                start, end = text.find(TABLE_START), text.find(TABLE_END)
                if start == -1 or end < start:
                    self.error(index, 1, "IX004", "в индексе нет маркеров таблицы содержимого")
                    continue
                end += len(TABLE_END)
                fresh = self.render_table(index)
                if text[start:end].strip() == fresh.strip():
                    continue
                if self.fix:
                    index.write_text(text[:start] + fresh + text[end:], encoding="utf-8", newline="\n")
                else:
                    self.error(
                        index, 1, "IX005",
                        "таблица содержимого разошлась с папкой. Запусти just fix",
                    )

    def check_index_form(self, path: Path, text: str) -> None:
        """Индекс состоит из frontmatter, заголовка и генерируемой таблицы.

        Правила ведения зоны в индексе не живут: для них есть отдельный раздел.
        Индекс, обросший правилами, перестают открывать ради оглавления, а
        правила в нём разъезжаются с теми, что лежат в своём месте.
        """
        if path.name != "index.md":
            return
        m = FRONTMATTER_RE.match(text)
        body = text[m.end():] if m else text
        start = body.find(TABLE_START)
        if start == -1:
            return
        head = body[:start]
        extra = [
            line for line in head.split(chr(10))
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if extra:
            self.error(
                path, 1, "IX006",
                f"в индексе есть текст помимо заголовка и таблицы: {extra[0][:60]}. "
                f"Правила зоны живут в docs/documentation, а не здесь",
            )

    def check_indexes(self) -> None:
        for root in DOC_ROOTS:
            if root in STRUCTURE_EXEMPT:
                continue
            base = ROOT / root
            if not base.exists():
                continue
            for directory in [base, *[d for d in base.rglob("*") if d.is_dir()]]:
                docs = [p for p in directory.glob("*.md") if p.name != "index.md"]
                subdirs = [d for d in directory.iterdir() if d.is_dir()]
                index = directory / "index.md"
                if index.exists():
                    continue
                if len(docs) > INDEX_THRESHOLD or subdirs:
                    reason = (
                        f"{len(docs)} документов" if len(docs) > INDEX_THRESHOLD
                        else f"{len(subdirs)} вложенных папок"
                    )
                    self.error(
                        directory / "index.md", 1, "IX001",
                        f"нет index.md, хотя в папке {reason}",
                    )

    def check_mermaid(self, path: Path, text: str) -> None:
        for line_no, info, body in iter_fences(text):
            if info != "mermaid":
                continue
            content = [line for line in body if line.strip() and not line.strip().startswith("%%")]
            if not content:
                self.error(path, line_no, "MM001", "пустой блок mermaid")
                continue
            first = content[0].strip()
            kind = first.split()[0].rstrip(";") if first.split() else ""
            if kind not in MERMAID_TYPES:
                self.error(
                    path, line_no, "MM002",
                    f"неизвестный тип диаграммы: {kind!r}. Блок не отрендерится",
                )
            unbalanced = sum(line.count("[") - line.count("]") for line in content)
            if unbalanced:
                self.warn(path, line_no, "MM003", "несбалансированные скобки в блоке mermaid")

    def check_state_leak(self, path: Path, text: str) -> None:
        """Чек-листы прогресса не должны лежать в docs/."""
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            return
        if rel.parts[0] != "docs":
            return
        for line_no, line in iter_lines_outside_fences(text):
            if CHECKBOX_RE.match(line):
                self.error(
                    path, line_no, "ST001",
                    "чек-лист в docs/. Состояние задач живёт в .work/, а не в документации",
                )
                return

    def check_placeholders(self, path: Path, text: str) -> None:
        for line_no, line in iter_lines_outside_fences(text):
            if PLACEHOLDER_RE.search(line):
                self.error(
                    path, line_no, "PH001",
                    "незаполненный плейсхолдер из шаблона",
                )
                return

    # -- вывод -----------------------------------------------------------

    def report(self) -> int:
        errors = [i for i in self.issues if i.level == "error"]
        warnings = [i for i in self.issues if i.level == "warning"]

        for issue in sorted(self.issues, key=lambda i: (str(i.path), i.line)):
            rel = issue.path.relative_to(ROOT) if issue.path.is_relative_to(ROOT) else issue.path
            mark = "ОШИБКА " if issue.level == "error" else "warning"
            print(f"{mark} {rel}:{issue.line} [{issue.code}] {issue.message}")

        print()
        print(f"Ошибок: {len(errors)}, предупреждений: {len(warnings)}")
        return 1 if errors else 0

    def report_github(self) -> int:
        errors = [i for i in self.issues if i.level == "error"]
        for issue in self.issues:
            rel = issue.path.relative_to(ROOT) if issue.path.is_relative_to(ROOT) else issue.path
            kind = "error" if issue.level == "error" else "warning"
            message = issue.message.replace("\n", "%0A")
            print(f"::{kind} file={rel},line={issue.line},title={issue.code}::{message}")
        return 1 if errors else 0


# -- вспомогательное -----------------------------------------------------


def iter_fences(text: str):
    """Отдаёт (номер строки, язык, строки тела) для каждого блока кода."""
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = FENCE_RE.match(lines[i])
        if m:
            fence = m.group(2)
            info = m.group(3)
            start = i + 1
            body: list[str] = []
            i += 1
            while i < len(lines):
                closing = FENCE_RE.match(lines[i])
                if closing and closing.group(2)[0] == fence[0] and len(closing.group(2)) >= len(fence) and not closing.group(3):
                    break
                body.append(lines[i])
                i += 1
            yield start, info, body
        i += 1


def iter_lines_outside_fences(text: str):
    """Отдаёт (номер строки, строка) только вне блоков кода."""
    inside = None
    for line_no, line in enumerate(text.split("\n"), start=1):
        m = FENCE_RE.match(line)
        if m:
            marker = m.group(2)
            if inside is None:
                inside = marker
            elif marker[0] == inside[0] and len(marker) >= len(inside) and not m.group(3):
                inside = None
            continue
        if inside is None:
            yield line_no, line


def iter_list_items(text: str):
    """Отдаёт (номер первой строки, текст пункта) для пунктов списка.

    Строки продолжения склеиваются: аннотация обычно не помещается в одну строку,
    и без склейки проверка видела бы только её начало.
    """
    item_start = re.compile(r"^\s*[-*+]\s+")
    lines = list(iter_lines_outside_fences(text))
    i = 0
    while i < len(lines):
        line_no, line = lines[i]
        if item_start.match(line):
            parts = [line]
            j = i + 1
            while j < len(lines):
                nxt_no, nxt = lines[j]
                if nxt_no != lines[j - 1][0] + 1:
                    break
                if not nxt.strip() or item_start.match(nxt) or not nxt.startswith((" ", "	")):
                    break
                parts.append(nxt.strip())
                j += 1
            yield line_no, " ".join(parts)
            i = j
        else:
            i += 1


def read_frontmatter(path: Path) -> dict:
    """Возвращает поля frontmatter, разобранные YAML-парсером."""
    m = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if not m:
        return {}
    try:
        fields = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return fields if isinstance(fields, dict) else {}


def slugify(heading: str) -> str:
    text = re.sub(r"[`*_]", "", heading).strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"[\s]+", "-", text)


def normalize(text: str) -> set[str]:
    words = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
    return {w for w in words if len(w) > 3}


def annotations_agree(annotation: str, description: str) -> bool:
    """Аннотация в индексе должна пересекаться по смыслу с description файла.

    Точное совпадение не требуется: в индексе к описанию добавляют «Читать когда ...».
    Проверяем, что значимые слова описания в аннотации присутствуют.
    """
    a, d = normalize(annotation), normalize(description)
    if not d:
        return True
    return len(a & d) / len(d) >= 0.4


def main() -> int:
    # Консоль Windows по умолчанию не в UTF-8, а сообщения на русском.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", action="store_true",
                        help="режим шаблона: плейсхолдеры {{...}} допустимы")
    parser.add_argument("--max-age", type=int, default=180,
                        help="порог протухания updated в днях")
    parser.add_argument("--fix", action="store_true",
                        help="пересобрать таблицы содержимого в index.md")
    parser.add_argument("--format", choices=["text", "github"], default="text")
    args = parser.parse_args()

    linter = Linter(template_mode=args.template, max_age=args.max_age, fix=args.fix)
    linter.run()
    if args.format == "github":
        return linter.report_github()
    return linter.report()


if __name__ == "__main__":
    sys.exit(main())
