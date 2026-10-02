"""Module for handling application startup, and close"""

import logging
from dataclasses import dataclass
from typing import Any

from mbu_rpa_core.exceptions import ProcessError

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Holds the running application(s) for use across the project."""

    app: Any | None = None


CONTEXT = AppContext()


def get_app() -> Any:
    """Return the running application instance."""
    if CONTEXT.app is None:
        raise ProcessError("Application not started - call startup() first")
    return CONTEXT.app


def startup():
    """Function for starting applications"""
    logger.info("Starting applications...")

    # Assign the started application so get_app() can reach it from anywhere:
    # CONTEXT.app = SolteqTandApp(path=..., username=..., password=...)
    # CONTEXT.app.start_application()
    # CONTEXT.app.login()


def soft_close():
    """Function for closing applications softly"""
    logger.info("Closing applications softly...")

    # if CONTEXT.app is not None:
    #     CONTEXT.app.close_application()

    CONTEXT.app = None


def hard_close():
    """Function for closing applications hard"""
    logger.info("Closing applications hard...")

    # Kill the process here, e.g. os.system("taskkill /f /im SolteqTand.exe")

    CONTEXT.app = None


def close():
    """Function for closing applications softly or hardly if necessary"""
    try:
        soft_close()
    except Exception:
        logger.warning("Soft close failed, forcing hard close", exc_info=True)
        hard_close()


def reset():
    """Function for resetting application"""
    logger.info("Resetting applications...")
    close()
    startup()
