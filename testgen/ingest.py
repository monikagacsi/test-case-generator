import re
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

ID_PATTERN = re.compile(r"\b([A-Za-z]{1,6}-\d+)\b")
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*\S)\s*$")
LEADING_NUM = re.compile(r"^\s*(\d+)[.)]\s*")
RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")


class Story(BaseModel):
    id: str
    title: str
    text: str
    domain: str = ""


# (heading text, body lines, domain)
Block = tuple[str, list[str], str]


def _blocks_by_heading(lines: list[str]) -> list[Block]:
    levels = Counter(len(m.group(1)) for l in lines if (m := HEADING.match(l)))
    if sum(levels.values()) < 2:
        return []
    # Story level = the most common heading level; ties go to the shallowest
    story_level = sorted(levels, key=lambda lv: (-levels[lv], lv))[0]

    blocks: list[Block] = []
    current: Block | None = None
    domain = ""
    for line in lines:
        m = HEADING.match(line)
        if m:
            level = len(m.group(1))
            if level == story_level:
                current = (m.group(2), [], domain)
                blocks.append(current)
                continue
            if level < story_level:
                current = None  # a higher-level heading ends the current story
                if level == story_level - 1 and level >= 2:
                    domain = m.group(2)
                continue
        if current is not None:
            current[1].append(line)
    return blocks


def _blocks_by_number(lines: list[str]) -> list[Block]:
    blocks: list[Block] = []
    current: Block | None = None
    for line in lines:
        m = NUMBERED.match(line)
        if m:
            current = (m.group(1), [], "")
            blocks.append(current)
        elif current is not None:
            current[1].append(line)
    return blocks if len(blocks) >= 2 else []


def _blocks_by_paragraph(text: str) -> list[Block]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [(p.splitlines()[0], p.splitlines()[1:], "") for p in paras]


def parse_stories(text: str) -> list[Story]:
    lines = text.splitlines()
    blocks = (
        _blocks_by_heading(lines)
        or _blocks_by_number(lines)
        or _blocks_by_paragraph(text)
    )

    stories: list[Story] = []
    seen: set[str] = set()
    for i, (head, body_lines, domain) in enumerate(blocks, start=1):
        body = "\n".join(l for l in body_lines if not RULE.match(l)).strip()

        num = LEADING_NUM.match(head)
        title = LEADING_NUM.sub("", head).strip()
        story_id = f"US-{int(num.group(1)):02d}" if num else f"US-{i:02d}"

        m = ID_PATTERN.search(title)
        if m:  # an explicit ID like "US-07: Login" wins
            story_id = m.group(1)
            title = (title[: m.start()] + title[m.end():]).strip(" :-–—.")
        if story_id in seen:
            story_id = f"{story_id}-{i}"
        seen.add(story_id)

        if not title:
            title = (body or head)[:60]
        stories.append(
            Story(id=story_id, title=title, text=f"{title}\n{body}".strip(), domain=domain)
        )
    return stories


def load_stories(path: str | Path) -> list[Story]:
    return parse_stories(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    for s in load_stories(sys.argv[1]):
        print(f"{s.id:6} | {s.domain[:28]:28} | {s.title[:50]}")
