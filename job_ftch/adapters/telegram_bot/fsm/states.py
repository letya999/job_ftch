from aiogram.fsm.state import State, StatesGroup


class AddingExamples(StatesGroup):
    positive = State()
    negative = State()


class AddingJobExamples(StatesGroup):
    positive = State()
    negative = State()


class AddingSources(StatesGroup):
    waiting = State()


class SettingSchedule(StatesGroup):
    waiting_for_interval = State()
