"""Read-only, single-terminal multi-guild monitoring dashboard.

Reads only logs/bot.log; never connects to Discord or writes user data.
Run alongside ONE bot process, not instead of it.
"""
import curses
from collections import deque
from pathlib import Path
import re
import time

from guild_log_parser import parse_line, GLOBAL, MAX_LINES

LOG = Path(__file__).resolve().parent / "logs" / "bot.log"


def safe_add(window, row, col, text, width, attr=0):
    if width <= 0:
        return
    try:
        window.addnstr(row, col, text, width, attr)
    except curses.error:
        pass


def run(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(400)
    guilds = {}
    recent = deque(maxlen=MAX_LINES)
    selection = 0
    file_key, offset = None, 0
    while True:
        # Tail incrementally; preserve current dashboard if a log rotates.
        try:
            stat = LOG.stat()
            key = (stat.st_dev, stat.st_ino)
            if key != file_key or stat.st_size < offset:
                file_key, offset = key, 0
            with LOG.open('rb') as stream:
                stream.seek(offset)
                chunk = stream.read()
                if chunk:
                    # Only consume complete lines to avoid half-written entries.
                    last_break = chunk.rfind(b'\n')
                    if last_break >= 0:
                        for raw in chunk[:last_break + 1].splitlines():
                            parse_line(raw.decode('utf-8', errors='replace'), guilds, recent)
                        offset += last_break + 1
        except FileNotFoundError:
            pass
        tabs = [GLOBAL] + sorted(guilds, key=lambda gid: guilds[gid]['name'].casefold())
        selection = min(selection, len(tabs) - 1)
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        if height < 8 or width < 35:
            safe_add(stdscr, 0, 0, 'Enlarge terminal (min 35x8)', max(0, width - 1))
        else:
            safe_add(stdscr, 0, 1, 'Activity Rewards | Multi-Guild Monitor (read only)', width - 2, curses.A_BOLD)
            safe_add(stdscr, 1, 1, f'Guilds detected: {len([x for x in guilds.values() if not x["removed"]])} | Log: {LOG.name} | [Tab/Right] Next  [Left] Previous  [q] Quit', width - 2)
            current = tabs[selection]
            labels = []
            for index, gid in enumerate(tabs):
                name = GLOBAL if gid == GLOBAL else guilds[gid]['name']
                labels.append(('[' + name + ']' if selection == index else name)[:24])
            safe_add(stdscr, 3, 1, '   '.join(labels), width - 2, curses.A_REVERSE)
            if current == GLOBAL:
                entries = recent
                description = 'All bot logs (including global errors)'
            else:
                item = guilds[current]
                entries = item['lines']
                description = f'{item["name"]} | ID: {current} | Members: {item["members"]} | {"Left server" if item["removed"] else "Connected on last sync"}'
            safe_add(stdscr, 4, 1, description, width - 2, curses.A_BOLD)
            safe_add(stdscr, 5, 1, '-' * (width - 2), width - 2)
            rows = max(0, height - 8)
            for n, line in enumerate(list(entries)[-rows:]):
                safe_add(stdscr, 6 + n, 1, line, width - 2)
            safe_add(stdscr, height - 1, 1, 'Logs only; no Discord connections, commands, or database writes.', width - 2, curses.A_DIM)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord('q'), ord('Q')):
            break
        if key in (9, curses.KEY_RIGHT):
            selection = (selection + 1) % len(tabs)
        if key in (curses.KEY_LEFT, curses.KEY_BTAB):
            selection = (selection - 1) % len(tabs)


if __name__ == '__main__':
    curses.wrapper(run)
