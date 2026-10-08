# Test Case Generator

Turn user stories and OpenAPI 3 specifications into reviewable test cases. The
Streamlit app generates functional, negative, edge, boundary, and API cases
with Gemini or a local Ollama model, then stores them in a SQLite review queue
for human review and export.

## How it works

The generator turns requirements into structured cases, checks that each case
has a valid shape and a specific expected result for every step, and flags
possible duplicates and low-value cases. QA professionals review the results
and decide what to edit, accept, reject, and export; generated tests are not
treated as ready-to-run tests.

## Get started

Requires Python 3.10 or later. From the project root, create an environment,
install dependencies, and copy the example configuration:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

For Gemini, set your API key in `.env`:

```dotenv
LLM_PROVIDER=gemini
LLM_API_KEY=your-gemini-api-key
```

Or install and start [Ollama](https://ollama.com/download), then configure
`.env` to use it:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=phi4-mini
```

Download the model if needed with `ollama pull phi4-mini`. Start the app:

```sh
python -m streamlit run app.py
```

## QA workflow

1. In **Test Generator**, paste a user story or upload a Markdown file with
   multiple stories. Each story can generate up to 10 cases across categories.
2. To generate API cases, upload an OpenAPI 3 YAML or JSON file. The generator
   uses operations that document a 2xx response.
3. In **Test Review**, filter the SQLite queue, inspect flags, and accept, edit,
   or reject cases. Generation appends cases without replacing earlier review
   decisions.
4. Export accepted cases as JSON, CSV, or Markdown. Edited cases return to
   pending review and must be accepted before export.

Try the included [user story samples](./samples/) or
[Pet Store OpenAPI sample](./samples/petstore_openapi.yaml).

<img width="770" height="742" alt="test-gen-img" src="https://github.com/user-attachments/assets/64471e68-1aec-4842-a304-9f3e4f38acb5" />


## Validation

Cases are validated against a typed schema. Every step must have exactly one
expected result; invalid model output is sent back for correction up to three
times. Heuristics flag likely duplicates within a story, generic titles, vague
expected results, too few meaningful steps, and boundary cases without concrete
test data. Flags prompt human review—they do not automatically reject cases.

## Limits and privacy

- Typed story titles are limited to 200 characters and story text to 900.
  Each story has a 10-case generation limit.
- OpenAPI support targets version 3 and local references. Operations without a
  documented success response are not assigned an invented status.
- Generated cases can be incomplete or incorrect. Review them before use; the
  app does not execute tests.
- Story content is sent to the selected model provider. Do not include secrets
  or sensitive data. Input safety checks are heuristic, not a security boundary.
- The Streamlit UI and single-user review queue are included. Authentication,
  multi-user support, RAG, test execution, fine-tuning, and a polished production
  frontend are out of scope.

## Run the tests

```sh
python -m pytest -q
```

The offline suite covers validation, generation and retry behavior, OpenAPI,
SQLite storage, the review UI, and exports. Model calls are mocked, so tests
need no API key or live model. GitHub Actions runs the suite on pushes and pull
requests.
