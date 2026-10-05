"""Module for general configurations of the process"""

MAX_RETRY = 10

# ----------------------
# Queue population settings
# ----------------------
MAX_CONCURRENCY = 100  # tune based on backend capacity
MAX_RETRIES = 3  # transient failure retries per item
RETRY_BASE_DELAY = 0.5  # seconds (exponential backoff)


# ----------------------
# Hvilke submissions kontrolleres
# ----------------------
# The three school-transport application forms. A submission to any of them is
# an application for kørsel and must become a bevilling in Befordringssystemet.
SUBMISSION_FORM_TYPES = (
    "ansoegning_om_koersel_med_skoleb",
    "ny_ansoegning_om_koersel_af_skol",
    "ny_ansoegning_om_midlertidig_koe",
)


# ----------------------
# How far back to look
# ----------------------
# THE MOST IMPORTANT SETTING IN THIS FILE.
#
# [RPA].[journalizing] holds every submission ever made to these forms. Without
# a cutoff the first run enqueues all of them and creates a bevilling for each
# — years of applications, every one of them landing on a caseworker's screen
# as new work.
#
# Set this to the moment the OS2Forms remote post handler is switched OFF.
# Before that moment OS2Forms creates the bevilling itself, and those rows
# carry no os2forms_id (the handler does not send one), so the duplicate guard
# in Befordringssystemet cannot recognise them — this process would create a
# second bevilling for every application already handled.
#
# Format: YYYY-MM-DD. The date itself is included.
EARLIEST_SUBMISSION_DATE = "2026-10-01"


# ----------------------
# Statuses that are skipped
# ----------------------
# A SECOND net under EARLIEST_SUBMISSION_DATE, not a replacement for it.
#
# Every submission handled before this process existed carries status
# 'Manual', so excluding it is an independent guard against the one mistake
# that matters here: a wrong cutoff date backfilling years of applications.
#
# Note the direction. This EXCLUDES a status rather than requiring one, and
# that is deliberate: if the journalization service renames a status or adds a
# state, this filter stops excluding and the run sees MORE rows — which the
# date cutoff and the os2forms_id guard then catch. An inclusion filter
# ('status must be Successful') would fail the other way and silently drop a
# citizen's application, which nothing downstream would ever notice.
#
# For the same reason the query treats a NULL status as "not excluded".
EXCLUDED_STATUSES = ("Manual",)


# ----------------------
# Check types
# ----------------------
# Each check contributes work items under its own prefix, so one workqueue
# serves all of them and process_item can dispatch on the type rather than
# guessing from the shape of the data. Named here because the prefix is part
# of the item reference and therefore part of the queue's dedup.
TYPE_CREATE_BEVILLING = "opret"
TYPE_ESDH_KEY = "esdh"


# ----------------------
# ESDH-key-kontrollen
# ----------------------
# A bevilling created from an OS2Forms submission has no ESDH key — the
# submission does not know the GO case. The nightly run derives esdh_url FROM
# the key, so without one the bevilling never gets a link into its case
# either, and nobody can set it by hand.
#
# Cheap today (a handful of rows), but it grows with every new application, so
# the cap is here from the start: each row costs a GO lookup.
ESDH_MAX_PER_RUN = 100

# The RPA database environment the GO credentials are read from.
RPA_DB_ENV = "PROD"


# ----------------------
# Timeouts
# ----------------------
# Generous: the create call resolves an address and a school, and a slow answer
# is better than a retry that risks a second bevilling.
API_TIMEOUT = 60
DB_TIMEOUT = 60
