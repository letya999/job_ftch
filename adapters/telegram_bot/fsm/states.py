"""FSM states for the Telegram bot."""

from aiogram.fsm.state import State, StatesGroup


class UploadStates(StatesGroup):
    """States for document upload process."""

    waiting_for_upload = State()


class UploadMode:
    """Upload modes constants (stored in FSM data)."""

    POSITIVE_RESUME = "positive_resume"
    NEGATIVE_RESUME = "negative_resume"
    POSITIVE_JOB = "positive_job"
    NEGATIVE_JOB = "negative_job"
