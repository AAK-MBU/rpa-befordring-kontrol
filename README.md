# ATS Process Framework

Template for RPA processes running on Automation Server (ATS). It provides the
queue population, processing loop, retry and error handling, so a new process only
needs to implement where its items come from and what to do with each one.

## How to use this template

This repository is tagged as a template. Create a new repository from it using the
[GitHub instructions](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template).

### Alternative: clone and remove the git bindings

Replace `<new-folder-name>` with your desired folder name:

```sh
git clone https://github.com/AAK-MBU/ATS_Process_Framework.git <new-folder-name>

cd <new-folder-name>

rm -rf .git
git init
git add .
git commit -m "Initial commit from ATS_Process_Framework"

git remote add origin <new-repo-url>
git push -u origin main
```

## Project structure

```
main.py                     entry point — parses --queue / --process / --finalize
ats_framework/
  core/                     framework code, normally unchanged
    application_handler.py  AppContext, startup / reset / close hooks
    error_handling.py       ErrorContext, handle_error, error mail
    finalize_process.py     post-processing hook
    process_item.py         per-item entry point
    queue_handler.py        queue population, concurrency, retries
  helpers/
    ats_functions.py        ATS API calls, item unpacking, logging
    config.py               retry, concurrency and backoff settings
  processes/                process-specific code — add your modules here
```

`ats_framework/core` and `ats_framework/helpers` come from the framework; keep
changes there to a minimum so updates can be pulled in. Everything specific to your
process goes in `ats_framework/processes`.

Split `ats_framework/processes` into subfolders once it grows — for example
`processes/sap/` or `processes/solteq/` — grouping by the system or step each module
touches.

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp .env.example .env
```

Fill in `.env`. `ATS_URL` and `ATS_TOKEN` are required — `ATS_URL` is commented out
in the example, and queue population raises `OSError` if it is missing.

## Running

The three phases are separate entry points:

```sh
uv run python main.py --queue      # populate the workqueue
uv run python main.py --process    # process items
uv run python main.py --finalize   # post-processing
```

## What to implement

Two functions in `ats_framework/core` carry the process-specific logic. Keep them
thin and delegate into `ats_framework/processes` rather than letting them grow:

- `core/queue_handler.retrieve_items_for_queue()` — return the items to queue, each
  as `{"reference": ..., "data": ...}`. References must be unique; items whose
  reference is already in the queue are skipped.
- `core/process_item.process_item(item_data, item_reference)` — do the work for one
  item. Delete the placeholder asserts.

Tuning (concurrency, retries, backoff) lives in `ats_framework/helpers/config.py`.

## Application handling

Processes that drive a desktop application (Solteq Tand, SAP, a browser) start it
once in `core/application_handler.startup()` and store it on `CONTEXT`:

```python
def startup():
    app = SolteqTandApp(...)
    app.start_application()
    app.login()
    CONTEXT.app = app
```

Any module can then reach it without passing it through every call:

```python
from ats_framework.core.application_handler import get_app


def open_patient(cpr: str):
    app = get_app()
    app.open_patient(cpr)
```

`get_app()` raises `ProcessError` if called before `startup()`. Because callers go
through `CONTEXT` rather than holding the instance themselves, `reset()` can replace
the application mid-run without leaving anyone with a dead handle.

Implement teardown in `soft_close()` (ask the application to exit cleanly) and
`hard_close()` (kill the process). `close()` tries soft first and falls back to hard,
and `reset()` is `close()` followed by `startup()`.

Note that this is shared state across the whole run. It suits one application and one
sequential item loop — it is not safe if items are ever processed concurrently.

## Work item structure

Items are stored flat, and `ats_framework/helpers/ats_functions.get_item_info`
unpacks them:

```json
{
  "reference": "<unique reference>",
  "data": { }
}
```

The reference is read from the work item's own field rather than from the payload.

## Error handling

- `BusinessError` — expected, item-level problems. The item is set to pending user
  action and the run continues.
- `ProcessError` — anything unexpected. The item fails, an error mail goes out, and
  `reset()` runs. After `MAX_RETRY` process errors the run stops.

## Contributing

CI runs ruff (lint and format) and requires the `version` in `pyproject.toml` to be
bumped on every pull request to `main`.