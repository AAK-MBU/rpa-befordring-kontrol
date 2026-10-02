"""Module to handle item processing"""

import logging

import requests
from mbu_rpa_core.exceptions import BusinessError, ProcessError

from ats_framework.processes import befordring_api, cpr_udtraek

logger = logging.getLogger(__name__)

# A 4xx means the submission is the problem; a 5xx means the API is.
_HTTP_CLIENT_FEJL = 400
_HTTP_SERVER_FEJL = 500


def process_item(item_data: dict, item_reference: str):
    """Create the bevilling for one submission.

    Args:
        item_data:
            The queue item's payload: form_id, form_type, form_data,
            form_submitted_date.

        item_reference:
            The OS2Forms submission id, which is also the item's reference.

    Raises:
        BusinessError:
            Where the submission itself is the problem and a human has to look
            at it — no CPR to be found, or an address or school the API cannot
            resolve. These go to pending_user rather than failing the run.

        ProcessError:
            Where the problem is ours or the API's and a retry may help.
    """

    form_type = item_data["form_type"]
    form_data = item_data["form_data"]

    cpr = cpr_udtraek.find_cpr(form_type, form_data)

    if not cpr:
        # Deliberately a BusinessError, not a skip. A submission nobody can
        # identify is an application from a real family that will otherwise
        # never reach a caseworker, and silence is how it stays lost.
        raise BusinessError(
            f"Ingen CPR kunne findes i formular {item_reference} ({form_type})"
        )

    payload = befordring_api.byg_payload(item_reference, form_type, form_data)

    try:
        resultat = befordring_api.opret_bevilling(cpr, payload)
    except requests.HTTPError as fejl:
        status = fejl.response.status_code if fejl.response is not None else None

        # 4xx is the submission: an address with no match in the register, a
        # school that is not in Skolematrikel. Retrying sends the same payload
        # to the same rules and gets the same answer, so a human is the only
        # way forward.
        if status is not None and _HTTP_CLIENT_FEJL <= status < _HTTP_SERVER_FEJL:
            raise BusinessError(str(fejl)) from fejl

        # 5xx, a timeout, a connection refused — the API's problem or the
        # network's. Worth another run.
        raise ProcessError(str(fejl)) from fejl
    except requests.RequestException as fejl:
        raise ProcessError(f"Kunne ikke nå Befordringssystemet: {fejl}") from fejl

    if resultat.get("status") == "already_exists":
        # Not an error. The duplicate guard did its job — most likely the
        # submission was handled before this item was queued.
        logger.info(
            "Formular %s havde allerede en bevilling (%s)",
            item_reference,
            resultat.get("result", {}).get("bevilling_id"),
        )
        return

    logger.info(
        "Oprettede bevilling for formular %s (CPR %s***): %s",
        item_reference,
        cpr[:6],
        resultat.get("result"),
    )
