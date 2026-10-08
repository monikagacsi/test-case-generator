import csv
import io
import json

from testgen.schema import ReviewItem, Status

CSV_FIELDS = [
    "id",
    "status",
    "manually_edited",
    "category",
    "priority",
    "title",
    "source_ref",
    "preconditions",
    "steps",
    "test_data",
    "expected_results",
    "api",
    "flags",
]


def approved_items(items: list[ReviewItem]) -> list[ReviewItem]:
    """Select only accepted cases for export."""
    return [
        item
        for item in items
        if item.status == Status.ACCEPTED
    ]


def export_json(items: list[ReviewItem]) -> str:
    """Serialize approved review items as formatted JSON."""
    return json.dumps(
        [item.model_dump(mode="json") for item in approved_items(items)],
        indent=2,
        ensure_ascii=False,
    )


def export_csv(items: list[ReviewItem]) -> str:
    """Serialize approved review items as CSV, encoding nested values as JSON."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()

    for item in approved_items(items):
        case = item.test_case
        writer.writerow(
            {
                "id": item.id,
                "status": item.status.value,
                "manually_edited": item.manually_edited,
                "category": case.category.value,
                "priority": case.priority.value,
                "title": case.title,
                "source_ref": case.source_ref,
                "preconditions": json.dumps(case.preconditions, ensure_ascii=False),
                "steps": json.dumps(case.steps, ensure_ascii=False),
                "test_data": (
                    json.dumps(case.test_data, ensure_ascii=False)
                    if case.test_data is not None
                    else ""
                ),
                "expected_results": json.dumps(
                    case.expected_results, ensure_ascii=False
                ),
                "api": (
                    json.dumps(case.api.model_dump(mode="json"), ensure_ascii=False)
                    if case.api is not None
                    else ""
                ),
                "flags": json.dumps(item.flags, ensure_ascii=False),
            }
        )
    return output.getvalue()


def _markdown_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _markdown_json(value: object) -> str:
    return f"`{json.dumps(value, ensure_ascii=False)}`"


def export_markdown(items: list[ReviewItem]) -> str:
    """Render approved review items as a readable Markdown document."""
    selected = approved_items(items)
    lines = ["# Approved test cases", ""]
    if not selected:
        lines.extend(["No accepted test cases.", ""])
        return "\n".join(lines)

    for item in selected:
        case = item.test_case
        lines.extend(
            [
                f"## {item.id}: {_markdown_text(case.title)}",
                "",
                f"- **Status:** {item.status.value}",
                f"- **Manually edited:** {'Yes' if item.manually_edited else 'No'}",
                f"- **Category:** {case.category.value}",
                f"- **Priority:** {case.priority.value}",
                f"- **Source:** {_markdown_text(case.source_ref)}",
                "",
                "**Preconditions**",
                "",
            ]
        )
        lines.extend(
            f"- {_markdown_text(value)}" for value in case.preconditions
        )
        if not case.preconditions:
            lines.append("- None")
        lines.extend(["", "**Steps**", ""])
        lines.extend(
            f"{index}. {_markdown_text(step)}"
            for index, step in enumerate(case.steps, start=1)
        )
        lines.extend(
            [
                "",
                "**Expected results**",
                "",
            ]
        )
        lines.extend(
            f"{index}. {_markdown_text(result)}"
            for index, result in enumerate(case.expected_results, start=1)
        )
        lines.extend(
            [
                "",
                "**Test data**",
                "",
                _markdown_json(case.test_data) if case.test_data is not None else "None",
            ]
        )
        if case.api is not None:
            lines.extend(
                [
                    "",
                    "**API details**",
                    "",
                    _markdown_json(case.api.model_dump(mode="json")),
                ]
            )
        if item.flags:
            lines.extend(["", "**Validation flags**", ""])
            lines.extend(f"- {_markdown_text(flag)}" for flag in item.flags)
        lines.append("")

    return "\n".join(lines)
