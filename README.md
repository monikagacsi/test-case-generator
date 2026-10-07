# test-case-generator

Generate functional, negative, edge, and boundary test cases from Markdown User
Stories with Gemini or a local Ollama model.

## Run with Gemini

1. Install dependencies with `python -m pip install -r requirements.txt`.
2. Set `LLM_PROVIDER=gemini` and your `LLM_API_KEY` in `.env`.
3. Generate cases for one story or all stories:

   ```sh
   python -m testgen.generate samples/user_stories.md US-01
   python -m testgen.generate samples/user_stories.md all
   ```

The generated cases are written to `output/generated.json`. Run the tests with
`python -m pytest`.

Temporary Gemini 503 availability errors are retried with backoff. If the
service remains unavailable, wait a few minutes and rerun the command.

## Run with Ollama

1. Install and start [Ollama](https://ollama.com/download).
2. Check which models are already installed:

   ```sh
   ollama list
   ```

3. Set these values in `.env`:

   ```dotenv
   LLM_PROVIDER=ollama
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=phi4-mini
   ```

   The Gemini API key is not used with Ollama.
4. Run the same generation command:

   ```sh
   python -m testgen.generate samples/user_stories.md US-01
   ```

The default model is `phi4-mini`. Change `OLLAMA_MODEL` to another model shown
by `ollama list`, or change `OLLAMA_BASE_URL` if Ollama is running on a
different host. If no model is installed, download one first:

```sh
ollama pull <model-name>
```