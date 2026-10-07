from testgen.schema import Category

SYSTEM_PROMPT = """You are a senior QA engineer who writes clear, concrete, \
independent test cases from user stories. You only test behavior that is stated \
or directly implied by the story. You never invent features. You always answer \
with valid JSON and nothing else."""

OUTPUT_FORMAT = """Return ONLY a JSON object in exactly this shape:

    {
        "test_cases": [
            {
                "title": "short, specific title",
                "category": "functional",
                "priority": "high | medium | low",
                "source_ref": "US-01",
                "preconditions": ["state that must be true before the test"],
                "steps": ["step 1", "step 2"],
                "test_data": {"field": "concrete value"},
                "expected_result": "specific, observable outcome",
                "api": null
            }
        ]
    }

    Rules:
        - Every test must be independent and runnable on its own.
        - Use concrete test data (real values), never placeholders like "valid input".
        - expected_result must be specific and observable, not "works correctly".
        - If the story is ambiguous, state your assumption in preconditions, starting with "Assumption:".
        - Do not repeat or lightly reword the same test. Fewer, better tests beat many similar ones.
        - If you cannot find enough meaningful tests for this category, return fewer."""

CATEGORY_PROMPTS = {
    Category.FUNCTIONAL: (
        "Category: FUNCTIONAL (happy paths). Cover the main success flow and every "
        "acceptance criterion at least once, using valid data and expected user behavior."
    ),
    Category.NEGATIVE: (
        "Category: NEGATIVE. Cover invalid inputs, forbidden or unauthorized actions, "
        "violated preconditions and error handling. The system should reject the action "
        "or show a clear error, and the expected result must say which."
    ),
    Category.EDGE: (
        "Category: EDGE CASES. Cover unusual but valid situations: concurrency, empty "
        "states, special characters, unusual sequences, interrupted flows, time-related "
        "behavior, and state transitions."
    ),
    Category.BOUNDARY: (
        "Category: BOUNDARY CONDITIONS. Focus on limits that appear in the story or its "
        "acceptance criteria (min, max, counts, lengths, time windows). For each limit, "
        "test just below, exactly at, and just above it, with the exact numbers in test_data. "
        "If the story has no measurable limits, return very few tests or none."
    ),
    Category.API: (
        "Category: API. Generate API-level tests only when the story states or directly "
        "implies an API contract. Include the HTTP method, path, and expected status in "
        "api details when they are known. Do not invent endpoints or request fields."
    ),
}
