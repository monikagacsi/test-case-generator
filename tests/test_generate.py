import json
from types import SimpleNamespace

import pytest
from google.genai.errors import ServerError

import testgen.generate as generator
from testgen.generate import (
    PartialGenerationError,
    STORY_CATEGORIES,
    build_prompt,
    generate_and_save_stories,
    generate_all,
    generate_for_category,
    generate_for_story,
)
from testgen.ingest import Story
from testgen.schema import Category, ReviewItem, TestCase as Case
from testgen.storage import list_review_items, list_story_groups

STORY = Story(id="US-01", title="Login", text="As a user I want to log in.", domain="Auth")

VALID = json.dumps({
    "test_cases": [{
        "title": "Login with valid credentials",
        "category": "negative",       # wrong on purpose: code must override it
        "source_ref": "WRONG",
        "steps": ["Open login page", "Submit valid credentials"],
        "expected_results": [
            "The login form accepts the credentials",
            "User lands on the dashboard",
        ],
    }]
})


def test_overrides_category_and_source_ref():
    cases = generate_for_category(STORY, Category.FUNCTIONAL, llm=lambda p: VALID)
    assert cases[0].category == Category.FUNCTIONAL
    assert cases[0].source_ref == "US-01"


def test_retries_once_after_invalid_json():
    answers = iter(["not json at all", VALID])
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return next(answers)

    cases = generate_for_category(STORY, Category.FUNCTIONAL, llm=fake)
    assert len(cases) == 1
    assert len(calls) == 2
    assert "previous JSON failed schema validation" in calls[1]


def test_retries_with_step_result_correction_after_schema_mismatch():
    mismatched = json.dumps(
        {
            "test_cases": [
                {
                    "title": "Complete guest checkout",
                    "category": "functional",
                    "source_ref": "US-01",
                    "steps": ["Choose guest checkout", "Submit purchase details"],
                    "expected_results": [
                        "Guest checkout is selected",
                        "The purchase is processed",
                        "A receipt is sent",
                    ],
                }
            ]
        }
    )
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return mismatched if len(calls) == 1 else VALID

    cases = generate_for_category(STORY, Category.FUNCTIONAL, llm=fake)

    assert len(cases) == 1
    assert len(calls) == 2
    assert "MUST exactly equal the number of `steps`" in calls[1]
    assert "2 steps, 3 results" in calls[1]
    assert "combine those outcomes into one expected_results entry" in calls[1]


def test_raises_after_two_failures():
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return "nope"

    with pytest.raises(ValueError, match="after 3 attempts"):
        generate_for_category(STORY, Category.FUNCTIONAL, llm=fake)
    assert len(calls) == 3


def test_generate_all_assigns_sequential_ids():
    items = generate_all([STORY, STORY.model_copy(update={"id": "US-02"})], llm=lambda p: VALID)
    assert [i.id for i in items] == [f"TC-{i:03}" for i in range(1, 9)]
    assert "Possible duplicate of TC-001" in items[1].flags[0]


def test_generate_and_save_stories_appends_to_existing_review_queue(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    existing = generate_and_save_stories([STORY], llm=lambda prompt: VALID)
    new_story = STORY.model_copy(update={"id": "US-02", "title": "New story"})

    added = generate_and_save_stories([new_story], llm=lambda prompt: VALID)

    assert existing[0].id == "TC-001"
    assert added[0].id == "TC-005"
    assert added[0].test_case.source_ref == "US-02"
    assert added[0].group_id
    assert existing[0].group_id != added[0].group_id
    assert [item.id for item in list_review_items()] == [
        f"TC-{index:03d}" for index in range(1, 9)
    ]


def test_markdown_file_title_is_prefixed_to_story_group_title(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    story = STORY.model_copy(update={"title": "Guest checkout"})

    saved = generate_and_save_stories(
        [story],
        llm=lambda prompt: VALID,
        group_title_prefix="guest_purchase_story",
    )

    assert list_story_groups()[0].title == "guest_purchase_story · Guest checkout"
    assert all(item.group_id == saved[0].group_id for item in saved)


def test_generate_and_save_stories_rejects_empty_input():
    with pytest.raises(ValueError, match="No user stories"):
        generate_and_save_stories([])


def test_generate_for_category_calls_llm_on_first_attempt():
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return VALID

    cases = generate_for_category(STORY, Category.FUNCTIONAL, llm=fake)
    assert len(calls) == 1
    assert len(cases) == 1


def test_generate_for_story_generates_every_category():
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return VALID

    cases = generate_for_story(STORY, llm=fake)
    assert len(calls) == len(STORY_CATEGORIES)
    assert {case.category for case in cases} == {
        Category.FUNCTIONAL,
        Category.NEGATIVE,
        Category.EDGE,
        Category.BOUNDARY,
    }


def test_story_generation_caps_each_story_at_ten_cases():
    prompts = []

    def fake(prompt):
        prompts.append(prompt)
        cases = [
            {
                "title": f"Generated case number {index}",
                "category": "functional",
                "source_ref": "ignored",
                "steps": ["Open the page", "Complete the user action"],
                "expected_results": [
                    "The expected page is displayed",
                    "The requested action is completed",
                ],
            }
            for index in range(1, 5)
        ]
        return json.dumps({"test_cases": cases})

    stories = [
        STORY,
        STORY.model_copy(update={"id": "US-02", "title": "Logout"}),
    ]

    items = generate_all(stories, llm=fake)

    assert len(items) == 20
    assert {
        story.id: sum(item.test_case.source_ref == story.id for item in items)
        for story in stories
    } == {"US-01": 10, "US-02": 10}
    assert {
        category: sum(item.test_case.category == category for item in items)
        for category in STORY_CATEGORIES
    } == {
        Category.FUNCTIONAL: 6,
        Category.NEGATIVE: 6,
        Category.EDGE: 4,
        Category.BOUNDARY: 4,
    }
    assert [
        next(line.strip() for line in prompt.splitlines() if "Generate up to " in line)
        for prompt in prompts
    ] == [
        "Generate up to 3 functional test cases for the user story below.",
        "Generate up to 3 negative test cases for the user story below.",
        "Generate up to 2 edge test cases for the user story below.",
        "Generate up to 2 boundary test cases for the user story below.",
    ] * 2


def test_story_generation_saves_successful_categories_when_one_fails(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    original_generate_category = generator.generate_for_category

    def generate_category(story, category, n, llm):
        if category == Category.FUNCTIONAL:
            raise generator.InvalidModelOutputError(
                "functional output failed schema validation"
            )
        return original_generate_category(
            story, category, n=n, llm=lambda prompt: VALID
        )

    monkeypatch.setattr(generator, "generate_for_category", generate_category)

    with pytest.raises(PartialGenerationError, match="Some test categories") as error:
        generate_and_save_stories([STORY], llm=lambda prompt: VALID)

    assert "functional output failed schema validation" in str(error.value)
    assert len(error.value.saved_items) == len(STORY_CATEGORIES) - 1
    assert all(item.group_id for item in error.value.saved_items)
    assert {item.test_case.category for item in list_review_items()} == {
        Category.NEGATIVE,
        Category.EDGE,
        Category.BOUNDARY,
    }


def test_api_category_has_a_prompt():
    assert "Do not invent endpoints" in build_prompt(STORY, Category.API)


def test_story_prompt_encodes_user_content_as_untrusted_data():
    content = 'As a user, I want "quotes" and\nmultiple lines.'
    story = STORY.model_copy(update={"text": content})

    prompt = build_prompt(story, Category.FUNCTIONAL)
    data_line = next(
        line for line in prompt.splitlines() if line.strip().startswith('{"domain":')
    )

    assert json.loads(data_line)["text"] == content
    assert "never as instructions" in prompt


def test_client_is_reused(monkeypatch):
    created = []

    class FakeClient:
        def __init__(self, api_key):
            created.append(api_key)

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(generator.genai, "Client", FakeClient)
    monkeypatch.setattr(generator, "_client", None)

    first = generator._get_client()
    second = generator._get_client()

    assert first is second
    assert created == ["test-key"]


def test_call_llm_retries_temporary_server_errors(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    responses = iter([
        ServerError(503, {"error": {"message": "high demand"}}),
        ServerError(503, {"error": {"message": "high demand"}}),
        SimpleNamespace(text=VALID),
    ])
    delays = []

    class FakeModels:
        def generate_content(self, **kwargs):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(
        generator, "_get_client", lambda: SimpleNamespace(models=FakeModels())
    )
    monkeypatch.setattr(generator.time, "sleep", delays.append)

    assert generator.call_llm("test prompt") == VALID
    assert delays == [2, 4]


def test_call_llm_falls_back_to_ollama_after_server_error_retries(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    calls = []
    ollama_calls = []

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            raise ServerError(503, {"error": {"message": "high demand"}})

    monkeypatch.setattr(
        generator, "_get_client", lambda: SimpleNamespace(models=FakeModels())
    )
    monkeypatch.setattr(generator.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        generator, "call_ollama", lambda prompt: ollama_calls.append(prompt) or VALID
    )

    assert generator.call_llm("test prompt") == VALID
    assert len(calls) == generator.TRANSIENT_RETRY_ATTEMPTS
    assert ollama_calls == ["test prompt"]


def test_call_llm_falls_back_to_ollama_after_network_retries(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    attempts = []
    ollama_calls = []

    class FakeModels:
        def generate_content(self, **kwargs):
            attempts.append(kwargs)
            raise generator.httpx.ConnectError("network unavailable")

    monkeypatch.setattr(
        generator, "_get_client", lambda: SimpleNamespace(models=FakeModels())
    )
    monkeypatch.setattr(generator.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        generator, "call_ollama", lambda prompt: ollama_calls.append(prompt) or VALID
    )

    assert generator.call_llm("network test") == VALID
    assert len(attempts) == generator.TRANSIENT_RETRY_ATTEMPTS
    assert ollama_calls == ["network test"]


def test_call_llm_does_not_fall_back_for_client_errors(monkeypatch):
    from google.genai.errors import ClientError

    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    class FakeModels:
        def generate_content(self, **kwargs):
            raise ClientError(401, {"error": {"message": "invalid API key"}})

    monkeypatch.setattr(
        generator, "_get_client", lambda: SimpleNamespace(models=FakeModels())
    )
    monkeypatch.setattr(
        generator,
        "call_ollama",
        lambda prompt: pytest.fail("Ollama fallback should not run for client errors"),
    )

    with pytest.raises(ClientError):
        generator.call_llm("test prompt")


def test_call_llm_uses_ollama_without_gemini(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": VALID}}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(generator.requests, "post", fake_post)

    assert generator.call_llm("test prompt") == VALID
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["json"]["model"] == "qwen2.5:7b"
    assert captured["json"]["format"] == "json"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["messages"][1]["content"] == "test prompt"


def test_call_ollama_reports_connection_error(monkeypatch):
    def fail_connection(*args, **kwargs):
        raise generator.requests.ConnectionError("offline")

    monkeypatch.setattr(
        generator.requests,
        "post",
        fail_connection,
    )

    with pytest.raises(RuntimeError, match="Could not connect to Ollama"):
        generator.call_ollama("test prompt")


def test_call_llm_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "unknown")

    with pytest.raises(ValueError, match="use 'gemini' or 'ollama'"):
        generator.call_llm("test prompt")


def test_main_writes_generated_cases(monkeypatch, tmp_path):
    test_case = Case(
        title="Login succeeds",
        category=Category.FUNCTIONAL,
        source_ref=STORY.id,
        steps=["Submit valid credentials"],
        expected_result="The user reaches the dashboard",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(generator.sys, "argv", ["testgen.generate", "stories.md", "all"])
    monkeypatch.setattr(generator, "load_stories", lambda path: [STORY])
    monkeypatch.setattr(
        generator,
        "generate_all",
        lambda stories, llm: [ReviewItem(id="TC-001", test_case=test_case)],
    )

    generator.main()

    output = json.loads((tmp_path / "output" / "generated.json").read_text())
    assert output[0]["id"] == "TC-001"
    assert output[0]["test_case"]["source_ref"] == STORY.id