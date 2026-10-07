import json
from types import SimpleNamespace

import pytest
from google.genai.errors import ServerError

import testgen.generate as generator
from testgen.generate import (
    STORY_CATEGORIES,
    build_prompt,
    generate_all,
    generate_for_category,
    generate_for_story,
)
from testgen.ingest import Story
from testgen.schema import Category, ReviewItem, TestCase as Case

STORY = Story(id="US-01", title="Login", text="As a user I want to log in.", domain="Auth")

VALID = json.dumps({
    "test_cases": [{
        "title": "Login with valid credentials",
        "category": "negative",       # wrong on purpose: code must override it
        "source_ref": "WRONG",
        "steps": ["Open login page", "Submit valid credentials"],
        "expected_result": "User lands on the dashboard",
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
    assert "previous answer was invalid" in calls[1]


def test_raises_after_two_failures():
    with pytest.raises(ValueError):
        generate_for_category(STORY, Category.FUNCTIONAL, llm=lambda p: "nope")


def test_generate_all_assigns_sequential_ids():
    items = generate_all([STORY, STORY.model_copy(update={"id": "US-02"})], llm=lambda p: VALID)
    assert [i.id for i in items] == [f"TC-{i:03}" for i in range(1, 9)]


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


def test_api_category_has_a_prompt():
    assert "Do not invent endpoints" in build_prompt(STORY, Category.API)


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


def test_call_llm_reports_server_error_after_retry_limit(monkeypatch):
    calls = []

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            raise ServerError(503, {"error": {"message": "high demand"}})

    monkeypatch.setattr(
        generator, "_get_client", lambda: SimpleNamespace(models=FakeModels())
    )
    monkeypatch.setattr(generator.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="temporarily unavailable \\(503\\)"):
        generator.call_llm("test prompt")
    assert len(calls) == generator.TRANSIENT_RETRY_ATTEMPTS


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
        lambda stories: [ReviewItem(id="TC-001", test_case=test_case)],
    )

    generator.main()

    output = json.loads((tmp_path / "output" / "generated.json").read_text())
    assert output[0]["id"] == "TC-001"
    assert output[0]["test_case"]["source_ref"] == STORY.id