from aiogram.fsm.state import State, StatesGroup


class AddingExamples(StatesGroup):
    positive = State()
    negative = State()


class AddingSources(StatesGroup):
    waiting = State()
