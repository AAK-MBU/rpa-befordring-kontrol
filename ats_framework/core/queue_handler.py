"""Module to hande queue population"""

import asyncio
import json
import logging

from automation_server_client import Workqueue

from ats_framework.helpers import config
from ats_framework.processes import journalizing

logger = logging.getLogger(__name__)


def retrieve_items_for_queue() -> list[dict]:
    """Every transport-form submission since the cutoff, as queue items.

    Returns:
        A list of {"reference": form_id, "data": {...}} dictionaries.

    Notes:
        No filtering happens here beyond form type and date. Deciding whether
        a submission still needs a bevilling is not this step's job, and there
        are already three layers that answer it:

          1. main.populate_queue skips references already in the workqueue, so
             a submission handled on an earlier run is never re-added.
          2. Befordringssystemet refuses a second bevilling for the same
             os2forms_id and answers "already_exists".
          3. A unique index enforces it in the database even if two runs
             overlap.

        The item carries the whole submission, not just an id. That is what
        keeps this process independent: process_item talks only to
        Befordringssystemet and never comes back here for more.
    """

    formularer = journalizing.hent_formularer()

    return [
        {
            "reference": formular["form_id"],
            "data": formular,
        }
        for formular in formularer
    ]


def create_sort_key(item: dict) -> str:
    """
    Create a sort key based on the entire JSON structure.
    Converts the item to a sorted JSON string for consistent ordering.
    """
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


async def concurrent_add(workqueue: Workqueue, items: list[dict]) -> None:
    """
    Populate the workqueue with items to be processed.
    Uses concurrency and retries with exponential backoff.

    Args:
        workqueue (Workqueue): The workqueue to populate.
        items (list[dict]): List of items to add to the queue.

    Returns:
        None

    Raises:
        Exception: If adding an item fails after all retries.
    """
    sem = asyncio.Semaphore(config.MAX_CONCURRENCY)

    async def add_one(it: dict):
        reference = str(it.get("reference") or "")
        data = it

        async with sem:
            for attempt in range(1, config.MAX_RETRIES + 1):
                try:
                    await asyncio.to_thread(workqueue.add_item, data, reference)
                    logger.info("Added item to queue with reference: %s", reference)
                    return True

                except Exception as e:
                    if attempt >= config.MAX_RETRIES:
                        logger.error(
                            "Failed to add item %s after %d attempts: %s",
                            reference,
                            attempt,
                            e,
                        )
                        return False

                    backoff = config.RETRY_BASE_DELAY * (2 ** (attempt - 1))

                    logger.warning(
                        "Error adding %s (attempt %d/%d). Retrying in %.2fs... %s",
                        reference,
                        attempt,
                        config.MAX_RETRIES,
                        backoff,
                        e,
                    )
                    await asyncio.sleep(backoff)

    if not items:
        logger.info("No new items to add.")
        return

    sorted_items = sorted(items, key=create_sort_key)
    logger.info(
        "Processing %d items sorted by complete JSON structure", len(sorted_items)
    )

    results = await asyncio.gather(*(add_one(i) for i in sorted_items))
    successes = sum(1 for r in results if r)
    failures = len(results) - successes

    logger.info(
        "Summary: %d succeeded, %d failed out of %d", successes, failures, len(results)
    )
