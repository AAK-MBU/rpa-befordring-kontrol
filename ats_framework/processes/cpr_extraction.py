"""Finding the student's CPR in a submission.

Each form asks for the CPR differently, so there is one rule per form. The
same logic lives in go_journalisering's extract_ssn, which needs it to find
the citizen's case — duplicated rather than shared because the two processes
are deliberately independent, and a shared module would be the coupling this
split exists to avoid.
"""

import logging

logger = logging.getLogger(__name__)


# The two forms that ask for the child directly: picked via MitID, or typed.
_CHILD_CPR_FIELDS = ("cpr_nummer_barn_mitid", "cpr_nummer_barn_manuelt")

# Midlertidig kørsel has four CPR fields, because three kinds of applicant use
# it:
#
#   a parent  — logs in with MitID (cpr_nummer_mitid is THEIR number), then
#               picks the child from a dropdown (cpr_nummer_barn_mitid) or
#               types it (cpr_nummer_barn_manuelt)
#   a teacher — logs in and types the pupil's number (cpr_nummer_elev)
#   the pupil — logs in themselves and fills none of the three above;
#               cpr_nummer_mitid IS the pupil
#
# So any of these three identifies the student directly...
_MIDLERTIDIG_STUDENT_CPR_FIELDS = (
    "cpr_nummer_elev",
    "cpr_nummer_barn_mitid",
    "cpr_nummer_barn_manuelt",
)

# ...and the logged-in user is only the student when none of them is filled.
# Reading this first would treat a parent's own CPR as the child's.
_MIDLERTIDIG_FALLBACK_FIELD = "cpr_nummer_mitid"


def _first_filled(data: dict, fields) -> str | None:
    """The first of `fields` that holds a value, with hyphens stripped."""

    for felt in fields:
        value = data.get(felt, "")

        if value:
            return str(value).replace("-", "").strip() or None

    return None


def find_cpr(form_type: str, form_data: dict) -> str | None:
    """The student's CPR for this submission, or None.

    Args:
        form_type:
            The OS2Forms webform id.

        form_data:
            The parsed submission, values under "data".

    Returns:
        The CPR without hyphens, or None where the form holds none.
    """

    data = form_data.get("data") or {}

    if form_type in (
        "ansoegning_om_koersel_med_skoleb",
        "ny_ansoegning_om_koersel_af_skol",
    ):
        return _first_filled(data, _CHILD_CPR_FIELDS)

    if form_type == "ny_ansoegning_om_midlertidig_koe":
        elev = _first_filled(data, _MIDLERTIDIG_STUDENT_CPR_FIELDS)

        if elev:
            return elev

        return _first_filled(data, (_MIDLERTIDIG_FALLBACK_FIELD,))

    logger.warning("Ukendt formulartype uden CPR-regel: %s", form_type)

    return None
