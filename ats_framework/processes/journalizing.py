"""Reading submissions from the journalization database.

What this reads, and what it deliberately does not:

    [RPA].[journalizing].[view_Journalizing] is a PUBLISHED VIEW. The tables
    behind it belong to the journalization service and carry its own state
    machine — status transitions, attempt counts, the response JSON it writes
    as it goes. Those are its internals and are not read here.

    The view answers one question: which submissions exist. That is a fact
    about the world rather than about their process, which is why it is safe
    to depend on. service-tandplejen-procesoverblik reads it the same way.

Status is deliberately NOT filtered on:

    A bevilling does not depend on the journalization having succeeded. They
    are separate obligations — the bevilling is a citizen's application that a
    caseworker must act on, the journalization is a records-management duty.

    Filtering on status would also mean adopting their status vocabulary: if
    'Successful' were renamed or a state added, this process would quietly
    stop seeing applications. Worse, a bug on their side would silently
    withhold citizens' applications from caseworkers, invisible from both
    sides.
"""

import json
import logging
import os

import pyodbc

from ats_framework.helpers import config

logger = logging.getLogger(__name__)


# form_submitted_date rather than anything status-derived: it is when the
# citizen applied, which is the only date this process has an opinion about.
_SELECT_FORMULARER = f"""
    SELECT
        form_id,
        form_type,
        form_data,
        CAST(form_submitted_date AS datetime) AS form_submitted_date
    FROM
        [RPA].[journalizing].[view_Journalizing]
    WHERE
        form_type IN ({", ".join("?" for _ in config.SUBMISSION_FORM_TYPES)})
    AND form_submitted_date >= ?
    /* ISNULL so a row with no status is KEPT. Written plainly as
       "status <> 'Manual'" the comparison would be UNKNOWN for NULL and the
       row would be dropped — turning a guard that is meant to fail open into
       one that silently discards applications. */
    AND ISNULL(status, N'') NOT IN ({", ".join("?" for _ in config.EXCLUDED_STATUSES)})
    ORDER BY
        form_submitted_date ASC
"""


def _connection_string() -> str:
    """The journalizing database connection string from the environment."""

    # Both spellings: .env.example declares DBConnectionString, while the
    # deployed services set it uppercase. Windows does not care, Linux does,
    # and this runs on both.
    conn_string = os.getenv("DBCONNECTIONSTRINGPROD")

    if not conn_string:
        raise OSError("DBCONNECTIONSTRINGPROD is not set in the environment")

    return conn_string


def get_submissions() -> list[dict]:
    """Every submission to the three transport forms since the cutoff.

    Returns:
        A list of dictionaries with form_id, form_type, form_data (parsed) and
        form_submitted_date (ISO string).

    Notes:
        Submissions whose form_data will not parse are skipped with an error
        log rather than taking the whole run down. One malformed row must not
        stop the other applications from reaching caseworkers.
    """

    params = [
        *config.SUBMISSION_FORM_TYPES,
        config.EARLIEST_SUBMISSION_DATE,
        *config.EXCLUDED_STATUSES,
    ]

    with pyodbc.connect(_connection_string(), timeout=config.DB_TIMEOUT) as conn:
        cursor = conn.cursor()
        cursor.execute(_SELECT_FORMULARER, params)
        columns = [kolonne[0] for kolonne in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    submissions = []

    for row in rows:
        try:
            form_data = json.loads(row["form_data"])
        except (TypeError, ValueError) as error:
            logger.error(
                "Springer formular %s over - form_data kunne ikke læses: %s",
                row.get("form_id"),
                error,
            )
            continue

        submissions.append(
            {
                "form_id": str(row["form_id"]),
                "form_type": row["form_type"],
                "form_data": form_data,
                "form_submitted_date": str(row["form_submitted_date"]),
            }
        )

    logger.info(
        "Fandt %d formular(er) af typerne %s indsendt %s eller senere (status %s springes over)",
        len(submissions),
        ", ".join(config.SUBMISSION_FORM_TYPES),
        config.EARLIEST_SUBMISSION_DATE,
        ", ".join(config.EXCLUDED_STATUSES),
    )

    return submissions
