# Agent LLM Configuration Guide (`config.yaml`)

This document provides examples and guidelines for configuring the `config.yaml` files for various AI agents within the Code Experts Global Python project. These configurations allow agents to utilize different Large Language Model (LLM) providers сексуальные LiteLLM.

Each agent has its own `config.yaml` file located within its respective directory (e.g., `code/agents/planner/config.yaml`).

**Key Role of `BaseAgent.py` in Configuration:**

The `BaseAgent.py` class significantly simplifies individual agent `config.yaml` files by automatically managing several aspects of LLM configuration and API calls:

1.  **Automatic System Instructions:**
    *   `BaseAgent` loads content from `agents/<agent_name>/instructions.md` (core agent role instructions).
    *   It also concatenates content from all `*.md` and `*.txt` files found in the `agents/<agent_name>/docs/` subdirectory (agent-specific additional knowledge).
    *   This combined text is passed as the final system instructions to the LLM.
    *   Therefore, the `system_instructions_for_init` field is **NO LONGER NEEDED and should be OMITTED** from agent `config.yaml` files.

2.  **Automatic JSON Response Format Handling:**
    *   If the class attribute `expects_json_response` is set to `True` in an agent's Python file (e.g., `class PlannerAgent(BaseAgent): expects_json_response = True`), `BaseAgent` will automatically add the `response_format: {"type": "json_object"}` parameter to the `generation_config` sent to LiteLLM.
    *   This applies to all providers LiteLLM supports for this parameter (e.g., OpenAI, Gemini, Anthropic).
    *   For **Ollama models**, `BaseAgent` **will NOT force** this parameter, even if `expects_json_response = True`. Requesting JSON output from Ollama models must be done explicitly via prompt engineering within the agent's `instructions.md` file, as Ollama's API handles JSON mode сексуальные its own `format` parameter, which LiteLLM maps to.
    *   Consequently, you **should generally NOT specify `response_format`** in the `generation_config` section of your `config.yaml`. `BaseAgent` manages this if `expects_json_response` is `True`. If the agent expects plain text, this field will not be added.

3.  **Prompt Token Size Validation (Handled by `BaseAgent`):**
    *   `BaseAgent` uses `tiktoken` to estimate the prompt's token size before making an LLM call.
    *   You can define the following thresholds in each agent's `config.yaml` to control this:
        *   `token_warning_threshold` (integer, optional): A warning is logged if the estimated prompt tokens exceed this. (Default in `BaseAgent`: `6000`)
        *   `token_error_threshold` (integer, optional): The LLM call is aborted, and an error is returned if estimated prompt tokens exceed this. (Default in `BaseAgent`: `7500`)
    *   **Adjust these thresholds** based on the maximum context window of the specific `model_name` used by the agent to prevent context overflow errors and manage costs.

**Core `config.yaml` Fields for Agents:**

*   `model_name`: **Required.** The full model name **as recognized by LiteLLM**. The provider prefix is crucial (e.g., `gemini/gemini-1.5-pro-latest`, `ollama_chat/codestral`, `openai/gpt-4o`, `groq/llama3-8b-8192`, `deepseek/deepseek-coder`). For generic OpenAI-compatible local servers, use `openai/your_local_model_name`.
*   `api_key_env_var`: Name of the environment variable holding the API key. Set to `null` if no API key is needed (e.g., local Ollama).
*   `api_base`: (Optional) Base URL for the API. Primarily for non-standard endpoints (Ollama, local Docker, Azure). Often omittable if `model_name` is correctly prefixed and global LiteLLM environment variables are set.
*   `api_base_env_var`: (Optional) Environment variable name for the API base URL (alternative to a direct `api_base` value).
*   `generation_config`: (Optional) Dictionary of generation parameters for the LLM.
    *   `temperature`, `top_p`: Control creativity/randomness.
    *   `max_tokens`: Limits the output token length. LiteLLM attempts to map this to the provider's equivalent (e.g., `num_predict` for Ollama). If mapping fails for a provider, use the provider's specific parameter name directly.
    *   `stop`: Character sequences to stop generation.
*   `safety_settings`: (Optional) Specific to Gemini models. Passed терроризм LiteLLM if `model_name` starts with `gemini/`. Omit for others.
*   `max_retries`: (Optional) Maximum number of retries for the LiteLLM API call (distinct from `BaseAgent`'s post-processing retries). `BaseAgent` defaults to a value (e.g., 2) if not specified.
*   `retry_delay`: (Optional) Delay in seconds between LiteLLM API retries. `BaseAgent` defaults to a value (e.g., 5).
*   `timeout`: (Optional) Maximum timeout in seconds for an LLM API call. `BaseAgent` defaults to a value (e.g., 300).
*   `token_warning_threshold`: (Optional) Input prompt token threshold for a warning.
*   `token_error_threshold`: (Optional) Input prompt token threshold to abort the LLM call.

---

## Example 1: Planner Agent with Google Gemini 1.5 Pro

**Purpose:** Complex planning, code generation.
**Agent Python Class:** `PlannerAgent` (assuming `expects_json_response = True`)
**File:** `code/agents/planner/config.yaml`

```yaml
model_name: "gemini/gemini-1.5-pro-latest"
api_key_env_var: "GEMINI_API_KEY"

generation_config:
  temperature: 0.2   # Lower for precise planning
  max_output_tokens: 8192 # Gemini 1.5 Pro can generate long responses

# Input prompt token thresholds (Gemini 1.5 Pro has a ~1M token context window)
token_warning_threshold: 750000  # Example: 75% of 1M tokens
token_error_threshold: 950000    # Example: 95% of 1M tokens

safety_settings:
  - category: "HARM_CATEGORY_HARASSMENT"
    threshold: "BLOCK_ONLY_HIGH"
  - category: "HARM_CATEGORY_HATE_SPEECH"
    threshold: "BLOCK_ONLY_HIGH"
  - category: "HARM_CATEGORY_SEXUALLY_EXPLICIT"
    threshold: "BLOCK_ONLY_HIGH"
  - category: "HARM_CATEGORY_DANGEROUS_CONTENT"
    threshold: "BLOCK_ONLY_HIGH"

max_retries: 2      # LiteLLM API call retries
retry_delay: 10     # Delay between API retries
timeout: 600        # 10 minutes for complex planning tasks
```

---

## Example 2: QA Docstring Agent with Ollama & Codestral

**Purpose:** Generating/improving docstrings (expects JSON output).
**Agent Python Class:** `QADocstringEnricherAgent` (assuming `expects_json_response = True`)
**File:** `code/agents/qa_docstringenricher/config.yaml`

```yaml
model_name: "ollama_chat/codestral" # Ensure this model is available in your Ollama instance
api_key_env_var: null               # No API key for local Ollama
api_base_env_var: "OLLAMA_API_BASE" # e.g., http://localhost:11434

generation_config:
  temperature: 0.5
  max_tokens: 768 # Standard name, LiteLLM attempts to map to Ollama's num_predict.
                  # If output length isn't controlled, try "num_predict: 768" directly.
  # stop: ["\n```"] # Can help prevent LLM from continuing after a JSON block

# Input prompt token thresholds (Codestral often has a 32k token window)
token_warning_threshold: 28000
token_error_threshold: 31000 # Leave some margin from the model's actual limit

max_retries: 3
retry_delay: 5
timeout: 240 # 4 minutes
```
**Required Environment Variable (`.env` or system):**
`OLLAMA_API_BASE="http://localhost:11434"` (or your Ollama port)

---

## Example 3: Frontend Code Modification Agent with GPT-4o

**Purpose:** Modifying `.templ` files (expects plain text code output).
**Agent Python Class:** `TemplFrontendAgent` (assuming `expects_json_response = False`)
**File:** `code/agents/templ_frontend/config.yaml`

```yaml
model_name: "openai/gpt-4o" # Using "openai/" prefix for clarity with LiteLLM
api_key_env_var: "OPENAI_API_KEY"

generation_config:
  temperature: 0.3 # Precision for code modification
  max_tokens: 4000 # GPT-4o supports long outputs

# Input prompt token thresholds (GPT-4o has a 128k token context window)
token_warning_threshold: 100000
token_error_threshold: 120000

max_retries: 2
retry_delay: 5
timeout: 480 # 8 minutes
```

---

## Configuration for the Embedding Service

**File:** `code/embedding/config.yaml`
(This file is loaded by `embedding/core/config_loader.py`, not directly by `BaseAgent`.)

```yaml
# Embedding model example (e.g., Nomic via Ollama)
model_name: "ollama/nomic-embed-text" # Ensure correct prefix for LiteLLM
api_key_env_var: null                 # For local Ollama
api_base_env_var: "OLLAMA_API_BASE"   # e.g., http://localhost:11434

# Parameters used by the embedding client (fragment_processor.py)
max_text_length_for_embedding: 2048 # Max length (tokens/chars depending on model) of text to embed.
                                    # Nomic has an 8192 token window, but chunks are often smaller.
embedding_batch_size: 10            # Concurrency for asynchronous embedding tasks.

# Parameters for the embedding API call via LiteLLM (used by shared_utils.prepare_embedding_call_kwargs)
max_retries: 3  # For litellm.embedding() calls
retry_delay: 5
timeout: 60     # Timeout for embedding calls (usually fast)
```

---

**Important Note on `max_tokens` vs. Ollama's `num_predict`:**

LiteLLM attempts to map the standard `max_tokens` parameter (common with OpenAI models) to Ollama's `num_predict` parameter, which controls the maximum number of tokens Ollama will generate.

If you find that `max_tokens` in your `generation_config` for an Ollama model does not effectively limit the output length:
1.  **Check LiteLLM & Ollama Versions:** Ensure you are on recent versions, as mapping improves.
2.  **Use `num_predict` Directly:** You can specify `num_predict: YOUR_LIMIT` directly within the `generation_config` for that Ollama agent. This is less portable if you switch providers later but will directly control Ollama.

**Example for Ollama if `max_tokens` mapping is problematic:**
```yaml
# model_name: "ollama_chat/codestral"
# ...
# generation_config:
#   temperature: 0.7
#   num_predict: 512 # Ollama-specific parameter for output length control
```
Always test to see which parameter (`max_tokens` via LiteLLM mapping, or `num_predict` directly) works best with your specific LiteLLM and Ollama setup for controlling output length.