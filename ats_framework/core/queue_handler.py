"""Module to hande queue population"""

import asyncio
import json
import logging

from automation_server_client import Workqueue

from ats_framework.helpers import ats_functions, config
from ats_framework.processes import befordring_api, journalizing

logger = logging.getLogger(__name__)


def retrieve_items_for_queue() -> list[dict]:
    """Everything the kontrol process has found to do, across all checks.

    Returns:
        A list of {"reference": "<type>:<key>", "data": {...}} dictionaries.

    Notes:
        References are TYPED — "opret:<form_id>", "esdh:<bevilling_id>" — so
        one workqueue serves every check without the keys colliding, and
        process_item dispatches on the type rather than guessing from the
        shape of the data. Adding a check means adding a function below and a
        branch there.
    """

    return [*_create_bevilling_items(), *_esdh_key_items()]


def _create_bevilling_items() -> list[dict]:
    """Submissions to the three transport forms that may need a bevilling.

    Notes:
        No filtering beyond form type and date. Whether a submission still
        needs a bevilling is answered downstream, three times over: the queue
        skips references it already holds, Befordringssystemet refuses a
        second bevilling for the same os2forms_id, and a unique index enforces
        it even if two runs overlap.

        The item carries the whole submission, so process_item talks only to
        Befordringssystemet and never comes back here for more.
    """

    return [
        {
            "reference": f"{config.TYPE_CREATE_BEVILLING}:{submission['form_id']}",
            "data": {"type": config.TYPE_CREATE_BEVILLING, **submission},
        }
        for submission in journalizing.get_submissions()
    ]


def _esdh_key_items() -> list[dict]:
    """Bevillinger missing their ESDH key or link, for resolution against GO.

    Notes:
        Unlike the create check, "not yet" is the NORMAL first answer here: a
        bevilling can exist before its submission has been journalized, and
        the GO case therefore before it exists. So these items have to be
        retried on later runs — see main.populate_queue, which drops a
        reference only when its earlier item COMPLETED. A simple
        "already in the queue" rule would try each bevilling exactly once, at
        the moment it is least likely to succeed.
    """

    bevillinger = befordring_api.get_bevillinger_missing_esdh()

    logger.info("Fandt %d bevilling(er) uden esdh-nøgle eller -link", len(bevillinger))

    return [
        {
            "reference": f"{config.TYPE_ESDH_KEY}:{bevilling['bevilling_id']}",
            "data": {"type": config.TYPE_ESDH_KEY, **bevilling},
        }
        for bevilling in bevillinger
    ]


# ATS item statuses that are terminal until something moves them. "new" and
# "in progress" are still going to be attempted, and "completed" succeeded —
# none of those three is this process's business.
_UNSUCCESSFUL_STATUSES = ("failed", "pending user action")


def reactivate_pending(existing: dict) -> int:
    """Put unresolved esdh items back in the queue, in place.

    Args:
        existing:
            {reference: workqueue row}, from get_workqueue_items(return_data=True).

    Returns:
        How many items were reactivated.

    Notes:
        Only the esdh check. Its "not yet" is the normal first answer — a
        bevilling can exist before its submission has been journalized, and so
        before the GO case does — so giving up after one attempt would try each
        bevilling at the moment it is LEAST likely to succeed.

        An opret item is deliberately left alone. A failure there means the
        submission itself needs a human (no CPR, an address matching nothing),
        and retrying every half hour would reproduce the same failure for ever.

        Reactivated IN PLACE rather than re-queued. Adding a second item with
        the same reference also works, but leaves the original behind: a
        bevilling resolved on the third run would show two permanent
        "pending user action" rows beside the one that succeeded, and only the
        last would be true.
    """

    reactivated = 0

    for reference, row in existing.items():
        if not reference.startswith(f"{config.TYPE_ESDH_KEY}:"):
            continue

        if str(row.get("status") or "").lower() not in _UNSUCCESSFUL_STATUSES:
            continue

        item_id = row.get("id")

        if item_id is None:
            continue

        if ats_functions.reactivate_item(item_id):
            reactivated += 1
            logger.info("Genaktiverede %s til nyt forsøg", reference)

    if reactivated:
        logger.info("Genaktiverede %d esdh-emne(r)", reactivated)

    return reactivated


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
