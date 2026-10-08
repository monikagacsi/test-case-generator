import re

from rapidfuzz import fuzz

from testgen.schema import Category, ReviewItem

DUPLICATE_SIMILARITY_THRESHOLD = 0.85

_NON_WORD = re.compile(r"[^a-z0-9]+")
_VAGUE_EXPECTED_RESULTS = (
    "works correctly",
    "work correctly",
    "functions correctly",
    "behaves as expected",
    "works as expected",
    "is successful",
)
_GENERIC_TITLES = {
    "test",
    "test functionality",
    "verify functionality",
    "check functionality",
    "test the feature",
    "verify the feature",
    "check the feature",
}
_VAGUE_STEPS = {
    "test",
    "verify",
    "check",
    "perform the action",
    "complete the flow",
    "verify the result",
}
_PLACEHOLDERS = {
    "example",
    "input",
    "test",
    "value",
    "valid input",
    "invalid input",
    "boundary value",
    "min",
    "max",
    "minimum",
    "maximum",
}


def _normalize(text: str) -> str:
    return " ".join(_NON_WORD.sub(" ", text.casefold()).split())


def _duplicate_text(item: ReviewItem) -> str:
    case = item.test_case
    return _normalize(" ".join([case.title, *case.steps, *case.expected_results]))


def _has_concrete_test_data(value: object) -> bool:
    if isinstance(value, dict):
        return bool(value) and all(_has_concrete_test_data(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(_has_concrete_test_data(v) for v in value)
    if isinstance(value, str):
        normalized = _normalize(value)
        return bool(normalized) and normalized not in _PLACEHOLDERS
    return value is not None


def _low_value_flags(item: ReviewItem) -> list[str]:
    case = item.test_case
    flags: list[str] = []

    if _normalize(case.title) in _GENERIC_TITLES:
        flags.append("Low value: title is too generic")

    expected = [_normalize(result) for result in case.expected_results]
    if any(
        phrase in result
        for result in expected
        for phrase in _VAGUE_EXPECTED_RESULTS
    ):
        flags.append("Low value: expected result is vague")

    meaningful_steps = [
        step for step in case.steps
        if len(_normalize(step).split()) >= 2 and _normalize(step) not in _VAGUE_STEPS
    ]
    if len(meaningful_steps) < 2:
        flags.append("Low value: fewer than 2 meaningful steps")

    if (
        case.category == Category.BOUNDARY
        and not _has_concrete_test_data(case.test_data)
    ):
        flags.append("Low value: boundary test has no concrete test data")

    return flags


def flag_test_cases(items: list[ReviewItem]) -> list[ReviewItem]:
    """Return review items with duplicate and low-value flags added."""
    flagged: list[ReviewItem] = []
    earlier_by_source: dict[str, list[tuple[str, str]]] = {}

    for item in items:
        flags = list(item.flags)
        case = item.test_case
        text = _duplicate_text(item)
        previous = earlier_by_source.setdefault(case.source_ref, [])

        if text:
            best_match: tuple[str, float] | None = None
            for previous_id, previous_text in previous:
                similarity = fuzz.ratio(text, previous_text) / 100
                if similarity >= DUPLICATE_SIMILARITY_THRESHOLD and (
                    best_match is None or similarity > best_match[1]
                ):
                    best_match = (previous_id, similarity)
            if best_match is not None:
                duplicate_id, similarity = best_match
                flags.append(
                    f"Possible duplicate of {duplicate_id} ({similarity:.2f} similarity)"
                )
            previous.append((item.id, text))

        flags.extend(_low_value_flags(item))
        flagged.append(item.model_copy(update={"flags": list(dict.fromkeys(flags))}))

    return flagged
