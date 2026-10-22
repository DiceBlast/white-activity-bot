import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / 'bot.log'

_CONFIGURED = False


class _ConsoleFilter(logging.Filter):
    def filter(self, record):
        return record.levelno >= logging.WARNING or record.name == 'activitybot.important'


def setup_logging():
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Remove handlers added by libraries or previous setup calls so terminal output
    # stays clean and messages are not duplicated.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8',
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(_ConsoleFilter())
    console_handler.setFormatter(
        logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S')
    )

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # discord.py is useful in the file, but routine HTTP/gateway chatter should not
    # flood the terminal. The console filter above still allows warnings/errors.
    logging.getLogger('discord').setLevel(logging.INFO)
    logging.getLogger('discord.http').setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name):
    return logging.getLogger(name)


def important(message, *args):
    logging.getLogger('activitybot.important').info(message, *args)
