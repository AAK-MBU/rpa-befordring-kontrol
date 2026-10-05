"""Module to handle item processing"""

import logging

import requests
from mbu_rpa_core.exceptions import BusinessError, ProcessError

from ats_framework.helpers import config
from ats_framework.processes import befordring_api, cpr_extraction, go_lookup

logger = logging.getLogger(__name__)

# A 4xx means the item is the problem; a 5xx means the API is.
_HTTP_CLIENT_FEJL = 400
_HTTP_SERVER_FEJL = 500


def process_item(item_data: dict, item_reference: str):
    """Handle one item, whichever check produced it.

    Args:
        item_data:
            The queue item's payload. "type" says which check it came from.

        item_reference:
            The typed reference, e.g. "opret:<form_id>" or "esdh:<bevilling_id>".

    Raises:
        BusinessError:
            Where the item itself needs a human.

        ProcessError:
            Where the problem is ours or an API's and a retry may help.
    """

    type_code = item_data.get("type", config.TYPE_CREATE_BEVILLING)

    if type_code == config.TYPE_CREATE_BEVILLING:
        _create_bevilling(item_data)
        return

    if type_code == config.TYPE_ESDH_KEY:
        _set_esdh_key(item_data)
        return

    raise BusinessError(f"Ukendt kontroltype '{type_code}' på {item_reference}")


def _call_api(funktion, *args, **kwargs):
    """Call an API function, mapping its failures onto the right exception.

    4xx is the item: a submission whose address matches nothing, a bevilling
    that no longer exists. The same request would get the same answer, so a
    human is the only way forward. Everything else — 5xx, timeout, connection
    refused — is worth another run.
    """

    try:
        return funktion(*args, **kwargs)
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else None

        if status is not None and _HTTP_CLIENT_FEJL <= status < _HTTP_SERVER_FEJL:
            raise BusinessError(str(error)) from error

        raise ProcessError(str(error)) from error
    except requests.RequestException as error:
        raise ProcessError(f"Kunne ikke nå Befordringssystemet: {error}") from error


def _create_bevilling(item_data: dict):
    """Create the bevilling for one submission."""

    form_id = item_data["form_id"]
    form_type = item_data["form_type"]
    form_data = item_data["form_data"]

    cpr = cpr_extraction.find_cpr(form_type, form_data)

    if not cpr:
        # Deliberately a BusinessError, not a skip. A submission nobody can
        # identify is an application from a real family that will otherwise
        # never reach a caseworker, and silence is how it stays lost.
        raise BusinessError(
            f"Ingen CPR kunne findes i formular {form_id} ({form_type})"
        )

    payload = befordring_api.build_payload(form_id, form_type, form_data)

    result = _call_api(befordring_api.create_bevilling, cpr, payload)

    if result.get("status") == "already_exists":
        # Not an error. The duplicate guard did its job.
        logger.info(
            "Formular %s havde allerede en bevilling (%s)",
            form_id,
            result.get("result", {}).get("bevilling_id"),
        )
        return

    logger.info(
        "Oprettede bevilling for submission %s (CPR %s***): %s",
        form_id,
        cpr[:6],
        result.get("result"),
    )


def _set_esdh_key(item_data: dict):
    """Resolve a bevilling's GO case and write the key onto it.

    Raises:
        BusinessError:
            Where GO has no befordring case for the student yet. That is the
            expected answer for a bevilling whose submission has not been
            journalized, so the item is offered again on later runs — see
            queue_handler.skal_springes_over.

    Notes:
        The link is resolved in the same pass. Setting only the key would
        leave the bevilling without a usable link until the nightly run, and
        the link is the part a caseworker actually clicks.

        A link that cannot be resolved is not an error: the key is still
        written, and the nightly run retries the link as its backstop. A
        missing link is a cosmetic gap; a missing key means the bevilling has
        no case at all.
    """

    bevilling_id = item_data["bevilling_id"]
    cpr = str(item_data["cpr_elev"]).replace("-", "")

    go = go_lookup.get_go_credentials()

    # A bevilling can already hold a key and be here only for its link —
    # kontrol writes the key even when GO's metadata call fails, because a
    # missing link is cosmetic while a missing key means no case at all.
    #
    # Reusing the key skips the contact lookup and the case search entirely:
    # two GO calls saved, and no risk of a second search landing on a
    # different case than the one the bevilling already names.
    case_id = (item_data.get("esdh_noegle") or "").strip()

    if not case_id:
        kontakt = go_lookup.find_contact(go, cpr)

        if kontakt is None:
            raise BusinessError(
                f"GO kender ikke CPR {cpr[:6]}*** - kan ikke finde sag til bevilling {bevilling_id}"
            )

        name, go_id = kontakt

        case_id = go_lookup.find_befordring_case(go, name, go_id, cpr)

        if case_id is None:
            raise BusinessError(
                f"Ingen befordringssag i GO for bevilling {bevilling_id} endnu "
                f"(CPR {cpr[:6]}***) - sagen er formentlig ikke journaliseret endnu"
            )

    case_url = go_lookup.find_case_url(go, case_id)

    if case_url is None and item_data.get("esdh_noegle"):
        # Nothing new to write: the key is already stored and the link is
        # still unresolvable. Raised rather than silently completed so the
        # item is offered again on the next run.
        raise BusinessError(
            f"Kunne ikke udlede link for bevilling {bevilling_id} ({case_id}) - prøves igen"
        )

    _call_api(befordring_api.set_esdh_key, bevilling_id, case_id, case_url)

    if case_url is None:
        logger.warning(
            "Satte esdh_noegle %s på bevilling %s, men kunne ikke udlede link - "
            "nattekørslen prøver igen",
            case_id,
            bevilling_id,
        )
        return

    logger.info("Satte esdh_noegle %s og link på bevilling %s", case_id, bevilling_id)
