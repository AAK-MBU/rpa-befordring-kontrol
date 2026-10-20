"""Module for handling errors"""

import json
import logging
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage

from automation_server_client import WorkItem
from mbu_rpa_core.database.connection import RPAConnection
from mbu_rpa_core.exceptions import BusinessError, ProcessError

logger = logging.getLogger(__name__)


@dataclass
class ErrorContext:
    """Context for error handling"""

    item: WorkItem | None = None
    action: Callable | None = None
    send_mail: bool = False
    process_name: str | None = None


def handle_error(
    error: ProcessError | BusinessError,
    log,
    context: ErrorContext | None = None,
) -> None:
    """
    Function to log error.
    Args:
        error (ProcessError | BusinessError): The error to handle.
        log (function): Logging function to log messages.
        context (ErrorContext): Context object containing additional parameters.
    Returns:
        None
    Raises:
        BusinessError: If a business logic error occurs.
        ProcessError: If a processing error occurs.
    """
    if context is None:
        context = ErrorContext()
    error_json = json.dumps(error.__dictinfo__())
    log_msg = f"Error: {error}"
    if context.item:
        log_msg = f"{repr(error)} raised for item: {context.item}. " + log_msg
        if context.action:
            context.action(error_json)
    log(log_msg)
    if context.send_mail:
        try:
            send_error_email(
                error=error,
                process_name=context.process_name,
            )
        except Exception as mail_error:
            # The mail is the notification, not the work.
            #
            # Caught for two reasons. It must not take the rest of the run
            # down: the item is already marked failed above, so an exception
            # escaping here leaves every item BEHIND this one unprocessed -
            # other citizens' applications, stopped by a mail server.
            #
            # And it must not fail quietly, which is the worse half. A
            # notification channel nobody knows is broken is worse than none
            # at all: the symptom is precisely "the item failed and nobody was
            # told", and without this line there is nothing in the log to say
            # the telling was what broke. Hence error level, with the
            # traceback.
            # Type and message inline, not only in exc_info. A log viewer that
            # shows one line per record would otherwise reduce this to "could
            # not send", which says nothing about which of the four steps in
            # send_error_email gave up.
            logger.error(
                "Kunne ikke sende fejlmail for %s (%s: %s) - fejlen ovenfor er "
                "stadig registreret på emnet",
                context.process_name,
                type(mail_error).__name__,
                mail_error,
                exc_info=True,
            )


def send_error_email(
    error: ProcessError | BusinessError,
    process_name: str | None = None,
) -> None:
    """
    Send email to defined recipient with error information
    Args:
        error (ProcessError | BusinessError): The error to include in the email.
        process_name (str | None): Name of the process where the error occurred.
    Returns:
        None
    Raises:
        Exception: If sending the email fails.

    Notes:
        No screenshot. The template this module came from attaches one, but
        that process drives a desktop application and this one does not - it
        talks to HTTP APIs and a database, and runs in a container with no X
        display at all. ImageGrab.grab() there does not return an empty image,
        it raises:

            OSError: X connection failed: error 5

        which used to happen AFTER the work item had been marked failed and
        before the mail was sent, so the only visible symptom was a failed
        item nobody was told about - and it took the rest of the run with it.

        There is also nothing to photograph. The errors that reach this mail
        are an HTTP 422 from Befordringssystemet or a GO lookup that found
        nothing; the desktop behind them shows the same nothing either way.
    """
    rpa_conn = RPAConnection(db_env="PROD", commit=False)
    with rpa_conn:
        error_email = rpa_conn.get_constant("Error Email")["value"]
        error_sender = rpa_conn.get_constant("Email Friend")[
            "value"
        ]  # Find in database...
        smtp_server = rpa_conn.get_constant("smtp_server")["value"]
        smtp_port = rpa_conn.get_constant("smtp_port")["value"]

    # Create message
    msg = EmailMessage()
    msg["to"] = error_email
    msg["from"] = error_sender
    # Named for what it is. The template called every one of these "Error
    # screenshot", which is now simply untrue, and a subject line is the only
    # part of this mail somebody reads before deciding whether to open it.
    msg["subject"] = f"Fejl: {process_name}" if process_name else "Fejl"

    # Create an HTML message with the exception
    error_dict = error.__dictinfo__()

    html_message = f"""
            <html>
                <body>
                    <p>Error type: {error_dict["type"]}</p>
                    <p>Error message: {error_dict["message"]}</p>
                    <p>{error_dict["traceback"]}</p>
                </body>
            </html>
        """

    msg.set_content("Please enable HTML to view this message.")
    msg.add_alternative(html_message, subtype="html")

    # Send message
    #
    # Named on the way out. Reaching this line proves the four constants
    # resolved, so a failure here is the hop to the mail server itself - a
    # blocked port, a server that will not do STARTTLS, a refused relay - and
    # the one line handle_error logs should be enough to tell them apart
    # without a second run.
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as smtp:
            smtp.starttls()
            smtp.send_message(msg)
    except Exception as error:
        raise RuntimeError(
            f"SMTP {smtp_server}:{smtp_port} -> {error_email}: "
            f"{type(error).__name__}: {error}"
        ) from error
