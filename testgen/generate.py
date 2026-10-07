import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError
from pydantic import ValidationError
import requests

from testgen.ingest import Story, load_stories
from testgen.prompts import CATEGORY_PROMPTS, OUTPUT_FORMAT, SYSTEM_PROMPT
from testgen.schema import Category, ReviewItem, TestCase, TestCaseSet

load_dotenv()

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_OLLAMA_MODEL = "phi4-mini"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_TIMEOUT_SECONDS = 300
STORY_CATEGORIES = [
    Category.FUNCTIONAL,
    Category.NEGATIVE,
    Category.EDGE,
    Category.BOUNDARY,
]
PAUSE_SECONDS = 4  # stay under free-tier rate limits
TRANSIENT_RETRY_ATTEMPTS = 3
TRANSIENT_RETRY_BACKOFF_SECONDS = 2

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["LLM_API_KEY"])
    return _client


def call_llm(prompt: str) -> str:
    """The only function that talks to the LLM provider. Swap providers here."""
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider == "ollama":
        return call_ollama(prompt)
    if provider != "gemini":
        raise ValueError(
            f"Unsupported LLM_PROVIDER {provider!r}; use 'gemini' or 'ollama'."
        )

    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    for attempt in range(TRANSIENT_RETRY_ATTEMPTS):
        try:
            response = _get_client().models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.4,
                    max_output_tokens=8192,
                ),
            )
            break
        except ServerError as e:
            if e.code != 503:
                raise
            if attempt == TRANSIENT_RETRY_ATTEMPTS - 1:
                raise RuntimeError(
                    "Gemini is temporarily unavailable (503). Please wait a few "
                    "minutes and run the command again."
                ) from e
            delay = TRANSIENT_RETRY_BACKOFF_SECONDS * (2**attempt)
            print(
                f"Gemini is temporarily unavailable (503); retrying in {delay} seconds "
                f"({attempt + 1}/{TRANSIENT_RETRY_ATTEMPTS - 1})...",
                file=sys.stderr,
            )
            time.sleep(delay)
    if not response.text:
        reason = response.candidates[0].finish_reason if response.candidates else "no candidates"
        raise RuntimeError(f"Model returned empty text (finish reason: {reason})")
    return response.text


def call_ollama(prompt: str) -> str:
    """Generate JSON test cases using a locally running Ollama server."""
    base_url = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).rstrip("/")
    model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    try:
        response = requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.4},
            },
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
    except requests.ConnectionError as e:
        raise RuntimeError(
            f"Could not connect to Ollama at {base_url}. Start Ollama and ensure "
            "the selected model has been downloaded."
        ) from e
    except requests.Timeout as e:
        raise RuntimeError(
            f"Ollama did not respond within {OLLAMA_TIMEOUT_SECONDS} seconds."
        ) from e

    response.raise_for_status()
    content = response.json().get("message", {}).get("content")
    if not content:
        raise RuntimeError("Ollama returned an empty response.")
    return content


def build_prompt(story: Story, category: Category, n: int = 4) -> str:
    return f"""Generate up to {n} {category.value} test cases for the user story below.

    {CATEGORY_PROMPTS[category]}

    Domain: {story.domain}
    Story ID: {story.id}

    --- USER STORY ---
    {story.text}
    --- END USER STORY ---

    {OUTPUT_FORMAT}"""


def generate_for_category(story: Story, category: Category, n: int = 4, llm=call_llm) -> list[TestCase]:
    prompt = build_prompt(story, category, n)
    last_error = None
    for _attempt in range(2):
        full_prompt = prompt
        if last_error:
            full_prompt += (
                f"\n\nYour previous answer was invalid:\n{last_error}\n"
                "Return corrected JSON only."
            )
        raw = llm(full_prompt)
        try:
            result = TestCaseSet.model_validate_json(raw)
        except ValidationError as e:
            last_error = str(e)[:1000]
            continue
        for tc in result.test_cases:
            tc.category = category
            tc.source_ref = story.id
        return result.test_cases
    raise ValueError(
        f"{story.id}/{category.value}: invalid output after retry: {last_error}"
    )


def generate_for_story(story: Story, categories=STORY_CATEGORIES, llm=call_llm) -> list[TestCase]:
    cases: list[TestCase] = []
    use_gemini = llm is call_llm and os.getenv("LLM_PROVIDER", "gemini").strip().lower() == "gemini"
    for index, category in enumerate(categories):
        batch = generate_for_category(story, category, llm=llm)
        print(f"  {story.id} {category.value}: {len(batch)} tests")
        cases.extend(batch)
        if use_gemini and index < len(categories) - 1:
            time.sleep(PAUSE_SECONDS)
    return cases


def generate_all(stories: list[Story], llm=call_llm) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for story in stories:
        for tc in generate_for_story(story, llm=llm):
            items.append(ReviewItem(id=f"TC-{len(items) + 1:03d}", test_case=tc))
    return items


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: python -m testgen.generate <stories.md> <US-01 | all>")
    stories = load_stories(sys.argv[1])
    wanted = sys.argv[2]
    if wanted != "all":
        stories = [s for s in stories if s.id == wanted]
        if not stories:
            sys.exit(f"No story with id {wanted}")

    items = generate_all(stories)

    out = Path("output")
    out.mkdir(exist_ok=True)
    (out / "generated.json").write_text(
        json.dumps([i.model_dump(mode="json") for i in items], indent=2), encoding="utf-8"
    )
    print(f"\n{len(items)} test cases saved to output/generated.json")
    for i in items:
        tc = i.test_case
        print(f"{i.id} [{tc.category.value:10}] {tc.title}")


if __name__ == "__main__":
    main()