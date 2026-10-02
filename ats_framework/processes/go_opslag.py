"""Looking up a student's befordring case in GetOrganized.

READ ONLY. This module finds cases; it never creates, reopens or modifies one.

That restriction is the whole point. go_journalisering's PPR flow is
find-OR-CREATE: where no case exists it makes the PPR case and the
"Kørsel til …" sub-case, and reopens a closed one. Creating cases is its job
and its alone. If this process created one too, a bevilling whose submission
has not been journalized yet would end up with two PPR cases for the same
child — one from each process, neither aware of the other.

So where nothing is found, the answer here is "not yet", and the bevilling is
left for a later run.
"""

import logging
import os
import re
import xml.etree.ElementTree as ET

import requests
from mbu_dev_shared_components.database.connection import RPAConnection
from mbu_dev_shared_components.getorganized import cases, contacts
from mbu_dev_shared_components.getorganized.objects import CaseDataJson
from requests_ntlm import HttpNtlmAuth

from ats_framework.helpers import config

logger = logging.getLogger(__name__)


# The case-type prefix befordring cases live under.
_SAGSTYPE = "PPR"

# The sub-case title every befordring case carries. Matched with "Contains"
# rather than equality, so the child's name in the title — diacritics,
# truncation, middle names — never affects the match.
_SUBCASE_TITEL = "Kørsel til "

# A befordring key is a sub-case — "PPR-2026-123456-001" — but the page a
# caseworker opens is the BASE case. Group 1 drops the sub-case suffix.
_PPR_SAG = re.compile(r"^(PPR-\d{4}-\d+)(?:-\d+)?$", re.IGNORECASE)

# GO answers the metadata call with JSON whose "Metadata" field is an XML row.
# The relative case path — "cases/PPR01/PPR-2026-123456" — is one of its
# attributes. PPR01 is a per-case system id that exists nowhere in the
# befordring database, which is the whole reason this lookup is needed.
_GO_CASE_URL_ATTRIB = "ows_CaseUrl"
_GO_SIDE = "SitePages/Home.aspx"

# GO has TWO hosts, and they are not the same:
#
#   ad.go.aarhuskommune.dk   the API. go_api_endpoint points here, and this
#                            process's account can reach it.
#   go.aarhuskommune.dk      the one caseworkers use in the browser.
#
# The link stored on the bevilling must point at the LATTER. Built from the
# API endpoint, a caseworker would get a link to a host she cannot reach —
# and it would look right until she clicked it.
_GO_BRUGER_BASE = os.getenv("GO_BROWSE_BASE", "https://go.aarhuskommune.dk").rstrip("/")


def hent_go_legitimation() -> tuple[str, str, str]:
    """GO endpoint, username and password from the RPA credential store.

    Returns:
        (endpoint, username, password).

    Notes:
        Read through RPAConnection rather than from the environment, so the
        credential lives in one place for every process that talks to GO —
        the same way go_journalisering and rpa-afgoerelsesbreve read it.
    """

    with RPAConnection(db_env=config.RPA_DB_ENV, commit=False) as rpa_conn:
        return (
            rpa_conn.get_constant("go_api_endpoint")["value"].rstrip("/"),
            rpa_conn.get_credential("go_api")["username"],
            rpa_conn.get_credential("go_api")["decrypted_password"],
        )


def find_kontakt(go: tuple[str, str, str], cpr: str) -> tuple[str, str] | None:
    """The citizen's name and GO contact id, or None if GO does not know them.

    Args:
        go:
            (endpoint, username, password) from hent_go_legitimation().

        cpr:
            The student's CPR, without hyphens.

    Returns:
        (full_name, go_id), or None.
    """

    endpoint, brugernavn, kodeord = go

    response = contacts.contact_lookup(
        cpr,
        f"{endpoint}/borgersager/_goapi/contacts/readitem",
        brugernavn,
        kodeord,
    )

    if not response.ok:
        logger.info(
            "GO kender ikke CPR %s*** endnu (%s)", cpr[:6], response.status_code
        )
        return None

    person = response.json()

    return person["FullName"], person["ID"]


def find_befordringssag(
    go: tuple[str, str, str], navn: str, go_id: str, cpr: str
) -> str | None:
    """The student's "Kørsel til …" sub-case id, or None.

    Args:
        go:
            (endpoint, username, password) from hent_go_legitimation().

        navn:
            The citizen's full name as GO holds it.

        go_id:
            The citizen's GO contact id.

        cpr:
            The student's CPR, without hyphens.

    Returns:
        A case id such as "PPR-2026-123456-001", or None where no befordring
        sub-case exists yet.

    Notes:
        Searched on the exact contact record plus a title containing
        "Kørsel til ", which is how go_journalisering finds the same case.

        Only the sub-case is looked for, never the parent PPR case. The parent
        is the child's whole PPR folder and covers far more than transport; a
        bevilling pointing at it would send a caseworker to the wrong place.
        Where only the parent exists, the sub-case has not been created yet and
        the honest answer is "not yet".
    """

    search_data = CaseDataJson().simple_search_case_data_json(
        case_type_prefix=_SAGSTYPE,
        field_properties={
            "ows_CCMContactData": {
                "value": f"{navn};#{go_id};#{cpr};#;#",
                "comparison": "Equal",
            },
            "ows_Title": {"value": _SUBCASE_TITEL, "comparison": "Contains"},
        },
        returned_cases_number="200",
    )

    endpoint, brugernavn, kodeord = go

    response = cases.find_case_by_case_properties(
        search_data,
        f"{endpoint}/_goapi/cases/findbycaseproperties",
        brugernavn,
        kodeord,
    )

    if not response.ok:
        logger.warning(
            "Sagssøgning fejlede for CPR %s*** (%s)", cpr[:6], response.status_code
        )
        return None

    # ItemExists is False for cases GO still lists but has deleted.
    fundne = [
        sag
        for sag in response.json().get("CasesInfo", [])
        if sag.get("ItemExists", True) is not False
    ]

    if not fundne:
        return None

    if len(fundne) > 1:
        # Should not happen — one befordring sub-case per child. Logged rather
        # than guessed quietly, because picking the wrong one puts a caseworker
        # in the wrong case.
        logger.warning(
            "CPR %s*** har %d befordringssager i GO: %s - bruger den første",
            cpr[:6],
            len(fundne),
            [sag.get("CaseID") for sag in fundne],
        )

    return fundne[0].get("CaseID")


def find_sags_url(go: tuple[str, str, str], sags_id: str) -> str | None:
    """The browser URL for a case, or None when it cannot be resolved.

    Args:
        go:
            (endpoint, username, password) from hent_go_legitimation().

        sags_id:
            A case key, with or without a sub-case suffix.

    Returns:
        The full page URL, or None.

    Notes:
        GET /_goapi/Cases/Metadata/<sag> answers with JSON whose "Metadata"
        field is an XML row. The relative path lives in its ows_CaseUrl
        attribute:

            ows_CaseUrl="cases/PPR01/PPR-2026-123456"

        PPR01 is a per-case system id, which is why the URL cannot be composed
        from the key alone — and why it is stored rather than derived.

        Looked up on the BASE case: the sub-case suffix does not change the
        page the link opens, and a student's bevillinger share it.

        Returns None on anything unexpected. A missing link is a cosmetic gap
        the next run retries; a wrong one sends a caseworker into another
        child's case.
    """

    match = _PPR_SAG.match(str(sags_id).strip())

    if match is None:
        logger.warning("Sags-id ser ikke ud som en PPR-sag: %r", sags_id)
        return None

    base = match.group(1)
    endpoint, brugernavn, kodeord = go

    try:
        response = requests.get(
            f"{endpoint}/_goapi/Cases/Metadata/{base}",
            headers={"Content-Type": "application/json"},
            auth=HttpNtlmAuth(brugernavn, kodeord),
            timeout=config.API_TIMEOUT,
        )
    except requests.RequestException as fejl:
        logger.warning("Kunne ikke hente metadata for %s: %s", base, fejl)
        return None

    if not response.ok:
        logger.warning(
            "HTTP %s fra GO for %s: %s",
            response.status_code,
            base,
            response.text[:200].strip(),
        )
        return None

    try:
        metadata = response.json().get("Metadata", "")
        relativ = ET.fromstring(metadata).attrib.get(_GO_CASE_URL_ATTRIB, "")
    except (ValueError, ET.ParseError) as fejl:
        logger.warning("Kunne ikke læse metadata for %s: %s", base, fejl)
        return None

    relativ = relativ.strip().strip("/")

    if not relativ:
        logger.warning("GO returnerede ingen %s for %s", _GO_CASE_URL_ATTRIB, base)
        return None

    # Built from the BROWSER host, not the API endpoint just called.
    return f"{_GO_BRUGER_BASE}/{relativ}/{_GO_SIDE}"
