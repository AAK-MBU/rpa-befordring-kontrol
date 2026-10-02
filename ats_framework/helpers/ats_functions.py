"""Helper module to call some functionality in Automation Server using the API."""

import logging
import os

import requests
from automation_server_client import WorkItem, Workqueue
from dotenv import load_dotenv


def get_workqueue_items(workqueue: Workqueue, return_data=False):
    """
    Retrieve items from the specified workqueue.
    If the queue is empty, return an empty list.
    """
    load_dotenv()

    url = os.getenv("ATS_URL")
    token = os.getenv("ATS_TOKEN")

    if not url or not token:
        raise OSError("ATS_URL or ATS_TOKEN is not set in the environment")

    headers = {"Authorization": f"Bearer {token}"}

    workqueue_items = {} if return_data else set()

    page = 1
    size = 200  # max allowed

    while True:
        full_url = f"{url}/workqueues/{workqueue.id}/items?page={page}&size={size}"
        response = requests.get(full_url, headers=headers, timeout=60)
        response.raise_for_status()

        res_json = response.json().get("items", [])

        if not res_json:
            break

        for row in res_json:
            ref = row.get("reference")
            if ref:
                if return_data:
                    workqueue_items[ref] = row
                else:
                    workqueue_items.add(ref)

        page += 1

    return workqueue_items


def genaktiver_item(item_id: int) -> bool:
    """Put a work item back in the queue by setting its status to "new".

    Args:
        item_id:
            The ATS work item id.

    Returns:
        True if ATS accepted the change.

    Notes:
        Used to retry an item IN PLACE. Adding a second item with the same
        reference would work too, but leaves the original behind for ever —
        a bevilling that took three runs to resolve would show two permanent
        "pending user action" rows next to the one that succeeded, and only
        the last of them would be true.

        --process pulls work through GET /workqueues/{id}/next_item, which
        only hands out items ATS still considers outstanding. An item parked
        at "pending user action" is deliberately outside that set, so moving
        it back to "new" is the only way to have it tried again.
    """

    load_dotenv()

    url = os.getenv("ATS_URL")
    token = os.getenv("ATS_TOKEN")

    if not url or not token:
        raise OSError("ATS_URL or ATS_TOKEN is not set in the environment")

    try:
        response = requests.put(
            f"{url}/workitems/{item_id}/status",
            headers={"Authorization": f"Bearer {token}"},
            json={"status": "new", "message": "Prøves igen"},
            timeout=60,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as fejl:
        # Logged rather than raised: failing to retry one item must not stop
        # the rest of the run from queueing new work.
        logging.getLogger(__name__).warning(
            "Kunne ikke genaktivere workitem %s: %s", item_id, fejl
        )
        return False


def get_item_info(item: WorkItem):
    """Unpack item"""
    return item.data["data"], item.reference


def init_logger():
    """Initialize the root logger with JSON formatting."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(module)s.%(funcName)s:%(lineno)d — %(message)s",
        datefmt="%H:%M:%S",
    )
