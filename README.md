# Code Experts - AI Multi-Agent Assistant for Code Modification and Quality

This project is a multi-agent LLM (Large Language Model) based system designed to assist with source code modification (currently targeting Go and `templ` templates) and to perform code quality analysis and improvements.

## Table of Contents

- [Code Experts - AI Multi-Agent Assistant for Code Modification and Quality](#code-experts---ai-multi-agent-assistant-for-code-modification-and-quality)
  - [Table of Contents](#table-of-contents)
  - [General Architecture](#general-architecture)
  - [Core Features](#core-features)
  - [Prerequisites](#prerequisites)
  - [Configuration](#configuration)
    - [Environment Variables (`.env`)](#environment-variables-env)
    - [Global Configuration (`global_config.py`)](#global-configuration-global_configpy)
    - [Agent Configuration (`agents/<agent_name>/config.yaml`)](#agent-configuration-agentsagent_nameconfigyaml)
    - [Embedding Service Configuration (`embedding/config.yaml`)](#embedding-service-configuration-embeddingconfigyaml)
  - [Installation](#installation)
  - [Usage](#usage)
    - [1. Generating the Code Manifest](#1-generating-the-code-manifest)
    - [2. Generating Code Embeddings](#2-generating-code-embeddings)
    - [3. Code Modification Workflow](#3-code-modification-workflow)
    - [4. Code Quality Pipeline](#4-code-quality-pipeline)
  - [Project Structure](#project-structure)
  - [Development and Contribution](#development-and-contribution)
  - [Troubleshooting](#troubleshooting)

## General Architecture

The system comprises several core modules orchestrated to achieve its objectives:

*   **AST Parser (Go):** A Go binary (`code/manifest/bin/ast_parser.go`) statically analyzes the target source code (Go projects) to extract code fragments (functions, types, methods, etc.) and their metadata, including docstrings and source location information for `.templ` files.
*   **Manifest Generator (`code/manifest/`):** A Python module that drives the AST parser and generates a `fragments_manifest.json`. This manifest is a structured representation of the codebase.
*   **Embedding Service (`code/embedding/`):**
    *   Generates vector representations (embeddings) for code fragments (based on their metadata and docstrings).
    *   Uses FAISS to store these embeddings and enable fast semantic similarity searches.
*   **AI Agents (`code/agents/`):**
    *   Each agent specializes in a specific task (planning, Templ code modification, docstring enrichment, proposing file splits, etc.).
    *   Each agent has its own LLM configuration (`config.yaml`) and system instructions (`instructions.md`).
    *   They use `LiteLLM` to interact with various LLM providers (OpenAI, Google Gemini, Ollama, Groq, etc.).
    *   `BaseAgent.py` provides a base class for common management of configurations, prompts, and LLM calls.
*   **Code Modification Orchestrator (`code/code_modifier/`):**
    *   Takes a user request in natural language to modify code.
    *   Uses the embedding service to find the most relevant code fragments.
    *   Invokes a `Planner` agent to generate a detailed action plan.
    *   Prepares a workspace (an isolated copy of the target project).
    *   Executes the plan step-by-step, calling specialized agents to modify code within the workspace.
    *   Attempts to compile/build the project after modifications and manages a correction cycle 실패 시.
    *   If successful, generates a diff report and an application plan, then applies changes to the original project (after backup).
*   **Code Quality Orchestrator (`code/code_quality/`):**
    *   Executes quality analysis tasks (e.g., checking/improving docstrings, identifying overly long files).
    *   Generates JSON reports with QA agent proposals.
    *   Allows re-running analysis for specific items.
    *   (Future) Will allow applying quality proposals after review.
*   **Shared Library (`code/lib/`):** Contains common utilities (logging, LiteLLM calls, file manipulation).

## Core Features

*   **Go and Templ Code Analysis** to extract a workable structure.
*   **Semantic Search** for code fragments relevant to a query.
*   **Development Task Planning** by an LLM agent.
*   **Automated Code Modification** by specialized LLM agents (currently focused on Go and Templ).
*   **Integrated Build/Test and Correction Cycle** in the modification workflow.
*   **Code Quality Analysis Pipeline** (docstring enrichment, file size analysis) with report generation.
*   **LLM Choice Flexibility** thanks to LiteLLM.

## Prerequisites

*   Python 3.10 or higher.
*   `pip` for Python package management.
*   A Go compiler (to compile `ast_parser.go` if you modify its source, or for the build command of your target project if it's a Go project).
*   The `templ` tool (if your target project uses Templ templates) for the `templ generate` command.
*   Access to one or more LLMs (via API or locally with Ollama).
*   Environment variables configured for LLM API keys.

## Configuration

Configuration is managed at several levels:

### Environment Variables (`.env`)

A `.env` file at the root of the project `code-experts-global-python/code/` (i.e., at the same level as `code_modifier`, `code_quality` directories) is used to store secrets and environment-specific configurations. It is loaded by `global_config.py`.

**Example `.env` content:**
```env
# Path to the root of your target Go/Templ project to be modified/analyzed
# (Relative to the 'code/' directory of this project, or an absolute path)
TARGET_PROJECT_PATH="../../your_target_go_project"

# API Keys for LLMs (adjust according to the providers you use)
GEMINI_API_KEY="AIzaSy..."
OPENAI_API_KEY="sk-..."
GROQ_API_KEY="gsk_..."
DEEPSEEK_API_KEY="sk-..."

# Endpoint for Ollama (if used)
OLLAMA_API_BASE="http://localhost:11434" # Adjust port if necessary

# Command to build/test your TARGET project (executed within TARGET_PROJECT_PATH)
# Used by the code modification orchestrator.
TARGET_BUILD_COMMAND="make build" # or "go build ./..." or "go test ./..."

# Maximum number of build/correction retries for the modification orchestrator
MAX_BUILD_RETRIES=3

# (Optional) Line threshold for the QAFileSplitterAgent
QA_FILE_SPLIT_MAX_LINES=600
```

### Global Configuration (`global_config.py`)

The file `code/global_config.py` loads variables from `.env` and defines constants used by multiple modules (like `TARGET_PROJECT_PATH`, `WORKSPACE_PATH`, `BUILD_COMMAND`). Ensure `TARGET_PROJECT_PATH` correctly points to the project you want the agents to modify or analyze.

### Agent Configuration (`agents/<agent_name>/config.yaml`)

Each agent in `code/agents/` has its own `config.yaml` file. This file defines:
*   `model_name`: The specific LLM model the agent will use (with provider prefix for LiteLLM).
*   `api_key_env_var`: The environment variable for the model's API key.
*   `api_base` / `api_base_env_var`: For non-standard LLM endpoints.
*   `generation_config`: Parameters like `temperature`, `max_tokens`.
*   `safety_settings`: For Gemini models.
*   `token_warning_threshold` / `token_error_threshold`: For prompt size validation.
*   `max_retries`, `retry_delay`, `timeout` for LiteLLM calls.

Refer to `LLM_Config.md` for detailed examples of agent configurations.

### Embedding Service Configuration (`embedding/config.yaml`)

The file `code/embedding/config.yaml` configures the model used for generating code embeddings. Its structure is similar to agent `config.yaml` files.

## Installation

1.  Clone this repository.
2.  Create and activate a Python virtual environment:
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Linux/macOS
    # venv\Scripts\activate    # On Windows
    ```
3.  Install Python dependencies:
    ```bash
    pip install -r code/requirements.txt
    ```
4.  Compile the Go AST parser (if you don't have it or modified `ast_parser.go`):
    ```bash
    cd code/manifest/bin
    go build ast_parser.go
    cd ../../.. 
    ```
    (Ensure the resulting binary `ast_parser.exe` or `ast_parser` is in `code/manifest/bin/` or at the root of `code/`).
5.  Create and configure your `.env` file in the `code/` directory as described above.
6.  Ensure `TARGET_PROJECT_PATH` in `.env` (or `global_config.py`) points to a valid Go/Templ project.

## Usage

The system revolves around several main commands, executed from the `code/` directory.

### 1. Generating the Code Manifest

This step analyzes your target project and creates `workspace/fragments_manifest.json`. **It is required before generating embeddings or running the orchestrators.**

```bash
python -m manifest.main
```
*   Options:
    *   `--target-project-path /path/to/target_project`: To override the target project path.
    *   `--output path/to/manifest.json`: To specify a different manifest output name/location.
    *   `--no-incremental`: Forces a full regeneration, ignoring an existing manifest.
    *   `--debug`: Enables debug logs for the manifest tool.

### 2. Generating Code Embeddings

This step reads `fragments_manifest.json` and generates embeddings for each fragment, saved to `workspace/fragment_embeddings.json`. **It is required before running the code modification orchestrator.**

```bash
python -m embedding.main
```
*   Options:
    *   `--force-rebuild`: Regenerates all embeddings, even if their `code_digest` hasn't changed.
    *   `--debug`: Enables debug logs.

### 3. Code Modification Workflow

This orchestrator takes a user request and attempts to modify the target code.
Entry point: `code_modifier.main`

**Syntax:**
```bash
python -m code_modifier.main "Your natural language modification request here" [OPTIONS]
```

**Main Options:**
*   `-w WORKSPACE_PATH`, `--workspace WORKSPACE_PATH`: Specifies the workspace directory.
*   `--manifest-file FILENAME`: Name of the manifest file to read from the workspace.
*   `--stop-after STAGE`: Stops the workflow after `optimization`, `planning`, `workspace_prep`, or `execution` (before finalization). Useful for debugging.
*   `--debug`: Detailed logs.

**Example:**
```bash
python -m code_modifier.main "Refactor the GetUserDetails function to also return the user's email address and update all its callers." --debug
```
This will run the full process: semantic selection, planning, copy to `workspace/current_project_state`, execution by agents, build/test, and if successful, generation of a diff report and application to the target project.

### 4. Code Quality Pipeline

This orchestrator executes quality analysis tasks and allows applying (currently simulated) the proposals.
Entry point: `code_quality.main`

**General Syntax:**
```bash
python -m code_quality.main <quality_action> [ACTION_OPTIONS] [GLOBAL_OPTIONS]
```

**Quality Actions:**

*   **`analyze`**: Analyzes code and generates JSON reports of quality proposals.
    ```bash
    python -m code_quality.main analyze --tasks <task_type> [--target-fragment ID | --target-file PATH] [--debug]
    ```
    *   `--tasks`: `docstrings`, `filesplit`, or `all` (default).
    *   `--target-fragment FRAGMENT_ID`: Targets a specific fragment for the `docstrings` task.
    *   `--target-file RELATIVE_FILE_PATH`: Targets a specific file for the `filesplit` task.

    Reports are saved in `workspace/quality_proposals/`.

*   **`retry_analysis`**: Re-runs analysis for a specific item or all failed items from a report.
    ```bash
    python -m code_quality.main retry_analysis --task-type <type> (--target-fragment ID | --target-file PATH | --input-report INPUT_REPORT.JSON) [--output-report OUTPUT_REPORT.JSON] [--debug]
    ```
    *   `--task-type`: `docstrings` or `filesplit` (required).
    *   `--target-fragment` OR `--target-file`: To target a single item.
    *   `--input-report`: If provided without a specific target, all items with status "error" in this report are retried. If provided with a target, that item in this report is updated.
    *   `--output-report`: Where to save the (new or updated) report. If omitted and `--input-report` is used, the original is overwritten (after backup).

*   **`apply`**: Applies proposals from a quality JSON report (currently in simulation mode).
    ```bash
    python -m code_quality.main apply [--report REPORT.JSON] [--task-type <type>] [--force] [--debug]
    ```
    *   `--report`: Specific report to apply. If omitted, uses the latest report of the `--task-type`.
    *   `--task-type`: `docstrings` or `filesplit`. Required if `--report` is omitted.
    *   `--force`: Apply without interactive confirmation (use with caution).

**Code Quality Pipeline Examples:**
```bash
# Analyze docstrings for all fragments and long files for splitting
python -m code_quality.main analyze --tasks all --debug

# Retry docstring analysis for a specific fragment, updating an existing report
python -m code_quality.main retry_analysis --task-type docstrings --target-fragment "my_fragment_id" --input-report "workspace/quality_proposals/docstrings_report_old.json" --output-report "workspace/quality_proposals/docstrings_report_updated.json"

# (Simulation) Apply proposals from the latest docstrings report
python -m code_quality.main apply --task-type docstrings --force
```

## Project Structure

```
code-experts-global-python/
├── code/
│   ├── agents/                     # Contains specialized AI agents
│   │   ├── <agent_name>/
│   │   │   ├── __init__.py
│   │   │   ├── agent.py            # Agent logic
│   │   │   ├── config.yaml         # Agent's LLM configuration
│   │   │   ├── instructions.md     # System instructions for agent's LLM
│   │   │   └── docs/               # Additional knowledge for agent
│   │   ├── base_agent.py           # Base class for all agents
│   │   ├── qa_docstringenricher/   # QA Agent for docstrings
│   │   └── qa_filesplitter/        # QA Agent for file splitting
│   │
│   ├── code_modifier/              # Orchestrator for code modification
│   │   ├── __init__.py
│   │   ├── main.py                 # Entry point
│   │   ├── cli.py                  # CLI arguments
│   │   └── core/                   # Modules for the modification workflow
│   │       ├── context_builder.py
│   │       ├── execution_loop.py
│   │       └── workflow_steps.py
│   │
│   ├── code_quality/               # Orchestrator for the quality pipeline
│   │   ├── __init__.py
│   │   ├── main.py                 # Entry point
│   │   ├── cli.py                  # CLI arguments
│   │   └── tasks/                  # Modules for each quality task
│   │       ├── docstring_task.py
│   │       ├── filesplit_task.py
│   │       └── base_quality_task.py
│   │       └── utils_quality.py
│   │
│   ├── embedding/                  # Embedding generation and search
│   │   ├── config.yaml
│   │   ├── main.py
│   │   └── core/
│   │
│   ├── lib/                        # Shared utilities
│   │   └── utils.py
│   │
│   ├── manifest/                   # Code manifest generation
│   │   ├── main.py
│   │   └── bin/
│   │       ├── ast_parser.go       # AST parser source
│   │       └── ast_parser          # Compiled binary
│   │
│   ├── workspace/                  # Generated data (NOT VERSIONED)
│   │   ├── fragments_manifest.json
│   │   ├── fragment_embeddings.json
│   │   ├── workflow_plan.json        (by code_modifier)
│   │   ├── current_project_state/    (working copy for code_modifier)
│   │   ├── backups/                  (backups before application by code_modifier)
│   │   ├── modification_reports/     (diff reports by code_modifier)
│   │   ├── quality_proposals/        (reports from QA agents)
│   │   ├── debug_outputs/            (debug files from agents)
│   │   └── *.log                     (log files for orchestrators)
│   │
│   ├── .env                        # Environment variables (NOT VERSIONED)
│   ├── global_config.py            # Global Python project configuration
│   └── requirements.txt            # Python dependencies
│
├── .gitignore
└── README.md                       # This file
└── LLM_Config.md                   # LLM configuration guide for agents
```

## Development and Contribution

*   Ensure `pre-commit` is installed and configured if using hooks (e.g., black, flake8).
*   Follow existing naming and style conventions.
*   Add unit and integration tests for new features.
*   Update documentation (`README.md`, `LLM_Config.md`, docstrings) when adding or modifying features.

## Troubleshooting

*   **`ModuleNotFoundError`**: Check your `PYTHONPATH` and ensure you are running commands from the `code/` directory. Verify that all necessary `__init__.py` files are present in package directories.
*   **LLM API Errors (LiteLLM)**:
    *   Verify your API keys and environment variables (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_API_BASE`, etc.).
    *   Consult LiteLLM logs (enable `--debug` for more details) to understand communication errors with the API.
    *   Ensure `model_name` in agent `config.yaml` files is correct and prefixed for LiteLLM.
*   **`ContextWindowExceededError` / Prompt Too Long**:
    *   Use `token_warning_threshold` and `token_error_threshold` in agent `config.yaml` files.
    *   For the `PlannerAgent`, reduce the number of fragments sent or the amount of code per fragment (via `context_builder.py`).
    *   For executor or QA agents, if the source file is too large, manual splitting or using `QAFileSplitterAgent` may be necessary.
*   **`ast_parser.go` Errors**: Ensure the binary is compiled and executable, and that `TARGET_PROJECT_PATH` points to a valid Go project. Check the parser's stderr logs.
*   **Permission Issues**: Ensure the script has write permissions in the `workspace/` directory and read/write permissions on `TARGET_PROJECT_PATH` (especially for finalization).