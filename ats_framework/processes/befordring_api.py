"""Calling Befordringssystemet.

The create endpoint takes the submission exactly as OS2Forms' own remote post
handler sent it: form-encoded, every value at the top level, authenticated with
X-API-Key. That shape is kept because the receiving end is unchanged.

The one addition is form_id. Befordringssystemet stores it as
Bevilling.os2forms_id behind a unique index, which is what makes a repeat call
safe: a submission that already produced a bevilling comes back as
"already_exists" instead of producing a second one.
"""

import logging
import os

import requests

from ats_framework.helpers import config

logger = logging.getLogger(__name__)


def _api() -> tuple[str, str]:
    """Endpoint and key from the environment."""

    endpoint = os.getenv("BEFORDRING_API_ENDPOINT")
    api_key = os.getenv("BEFORDRING_API_KEY")

    if not endpoint or not api_key:
        raise OSError(
            "BEFORDRING_API_ENDPOINT or BEFORDRING_API_KEY is not set in the environment"
        )

    return endpoint.rstrip("/"), api_key


def byg_payload(form_id: str, form_type: str, form_data: dict) -> dict:
    """Flatten a stored submission into the shape the API expects.

    Args:
        form_id:
            The OS2Forms submission id. Sent so the bevilling can be tied back
            to this submission, and so a repeat call is recognised.

        form_type:
            The webform id, sent as webform_id.

        form_data:
            The submission as the journalization view stores it — values under
            "data", submission metadata under "entity".

    Returns:
        A flat dictionary ready to post.

    Notes:
        The nesting is the difference between what is stored and what the API
        reads. Befordringssystemet asks for payload.get("webform_id") and
        payload.get("dato_for_foerste_koersel") at the top level, so a nested
        payload reads as entirely empty — every field missing, no error.

        "completed" becomes the bevilling's ansoegningsdato. Dug out
        defensively: a shape that does not match must not cost the bevilling,
        since the date can be corrected afterwards but a lost application
        cannot.
    """

    payload = dict(form_data.get("data") or {})

    payload["webform_id"] = form_type
    payload["form_id"] = form_id

    try:
        payload["completed"] = form_data["entity"]["completed"][0]["value"]
    except (KeyError, IndexError, TypeError):
        logger.warning(
            "Formular %s har ingen completed-dato - bevillingen oprettes uden ansøgningsdato",
            form_id,
        )

    return payload


def opret_bevilling(cpr: str, payload: dict) -> dict:
    """Create the bevilling for one submission.

    Args:
        cpr:
            The student's CPR.

        payload:
            The flattened submission from byg_payload.

    Returns:
        The API's JSON response. "status" is "created" or "already_exists".

    Raises:
        requests.HTTPError:
            If Befordringssystemet answers with an error status. The body is
            included, because it carries the real reason — an unmatched
            address, an unknown school — while the status code alone never
            says what to correct.
    """

    endpoint, api_key = _api()

    response = requests.post(
        f"{endpoint}/os2forms/create_bevilling/{cpr}",
        data=payload,
        headers={"X-API-Key": api_key},
        timeout=config.API_TIMEOUT,
    )

    if not response.ok:
        raise requests.HTTPError(
            f"{response.status_code} fra create_bevilling: {response.text}",
            response=response,
        )

    try:
        return response.json()
    except ValueError:
        return {"status": "ukendt", "raw": response.text}
