import re
import sys
from uuid import uuid4
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

ID_PATTERN = re.compile(r"\b([A-Za-z]{1,6}-\d+)\b")
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*\S)\s*$")
LEADING_NUM = re.compile(r"^\s*(\d+)[.)]\s*")
RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
MAX_STORY_TITLE_CHARS = 200
MAX_STORY_TEXT_CHARS = 900
MAX_IMPORTED_STORY_TEXT_CHARS = 10_000
UNSAFE_STORY_PATTERNS = (
    re.compile(
        r"""(?:['"]\s*(?:OR|AND)\s+['"]?\w+['"]?\s*=\s*['"]?\w+|"""
        r"""(?:\bOR\b|\bAND\b)\s+\d+\s*=\s*\d+)""",
        re.IGNORECASE,
    ),
    re.compile(r"\bUNION\s+(?:ALL\s+)?SELECT\b", re.IGNORECASE),
    re.compile(r"\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE)\b", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\s+\w+", re.IGNORECASE),
    re.compile(r"\bINSERT\s+INTO\s+\w+", re.IGNORECASE),
    re.compile(r"\bUPDATE\s+\w+\s+SET\b", re.IGNORECASE),
    re.compile(
        r"""(?:;\s*--|/\*.*?\*/|['"]\s*(?:--|#|/\*)|['"]\s*;\s*--)""",
        re.IGNORECASE,
    ),
    re.compile(r"<\s*(?:script|iframe|object|embed)\b", re.IGNORECASE),
    re.compile(r"\bjavascript\s*:", re.IGNORECASE),
    re.compile(r"\bon[a-z]+\s*=\s*['\"]?", re.IGNORECASE),
    re.compile(r"\b(?:eval|exec|__import__)\s*\(", re.IGNORECASE),
    re.compile(r"(?:^|[;&|]\s*)(?:sudo\s+)?rm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sh|bash)\b", re.IGNORECASE),
)
UNSAFE_STORY_MESSAGE = (
    "Story content contains a potentially executable code or injection pattern. "
    "Remove the payload and describe the expected behavior in plain language."
)


def _validate_story_input(*values: str) -> None:
    if any(
        pattern.search(value)
        for value in values
        for pattern in UNSAFE_STORY_PATTERNS
    ):
        raise ValueError(UNSAFE_STORY_MESSAGE)


class Story(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=MAX_STORY_TITLE_CHARS)
    text: str = Field(min_length=1)
    domain: str = Field(default="", max_length=MAX_STORY_TITLE_CHARS)


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


def _labeled_user_story_block(text: str) -> Block | None:
    lines = text.splitlines()
    label_pattern = re.compile(r"^\s*\*\*User Story:\*\*\s*(.*)$", re.IGNORECASE)
    label = next(
        (
            (index, match)
            for index, line in enumerate(lines)
            if (match := label_pattern.match(line))
        ),
        None,
    )
    if label is None:
        return None

    label_index, match = label
    story_lines = [match.group(1).strip()] if match.group(1).strip() else []
    for line in lines[label_index + 1:]:
        stripped = line.strip()
        if not stripped:
            if story_lines:
                break
            continue
        if re.match(r"^\*{0,2}Acceptance Criteria:\*{0,2}$", stripped, re.IGNORECASE):
            break
        story_lines.append(stripped)

    story_sentence = " ".join(story_lines)
    title = re.sub(r"\*\*(.*?)\*\*", r"\1", story_sentence).strip()
    title = re.sub(r"\s+", " ", title)[:MAX_STORY_TITLE_CHARS].strip()
    body = "\n".join(
        line for index, line in enumerate(lines)
        if index != label_index
    ).strip()
    return (title or "User Story", body.splitlines(), "")


def parse_stories(text: str) -> list[Story]:
    lines = text.splitlines()
    labeled_story = _labeled_user_story_block(text)
    blocks = (
        _blocks_by_heading(lines)
        or _blocks_by_number(lines)
        or ([labeled_story] if labeled_story is not None else [])
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
        story_text = f"{title}\n{body}".strip()
        if len(story_text) > (
            MAX_IMPORTED_STORY_TEXT_CHARS + MAX_STORY_TITLE_CHARS + 1
        ):
            raise ValueError(
                "Each Markdown user story must be "
                f"{MAX_IMPORTED_STORY_TEXT_CHARS:,} characters or fewer."
            )
        _validate_story_input(title, body, domain)
        stories.append(
            Story(id=story_id, title=title, text=story_text, domain=domain)
        )
    return stories


def story_from_text(title: str, text: str) -> Story:
    """Create one uniquely identified story from user-provided text."""
    normalized_title = title.strip()
    normalized_text = text.strip()
    if not normalized_title:
        raise ValueError("A user story title is required.")
    if not normalized_text:
        raise ValueError("User story text cannot be empty.")
    if len(normalized_text) > MAX_STORY_TEXT_CHARS:
        raise ValueError(
            f"User story text must be {MAX_STORY_TEXT_CHARS:,} characters or fewer."
        )
    _validate_story_input(normalized_title, normalized_text)
    return Story(
        id=f"US-TEXT-{uuid4().hex[:8]}",
        title=normalized_title,
        text=f"{normalized_title}\n{normalized_text}",
    )


def load_stories(path: str | Path) -> list[Story]:
    return parse_stories(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    for s in load_stories(sys.argv[1]):
        print(f"{s.id:6} | {s.domain[:28]:28} | {s.title[:50]}")
