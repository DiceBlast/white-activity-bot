"""Shared log parsing for the Tk and terminal dashboards (no curses dependency)."""
from collections import deque
import re

GUILD_ID = re.compile(r"\bguild_id=(\d+)\b")
PRESENT = re.compile(r"GUILD PRESENT \| guild_id=(\d+) \| name=(.*?) \| members=(\d+)")
REMOVED = re.compile(r"GUILD REMOVED \| guild_id=(\d+)")
GLOBAL = "Overview"
MAX_LINES = 250

def parse_line(line, guilds, global_lines):
    """Update in-memory display only; returns guild ID for recognized lines."""
    line = line.strip()
    if not line:
        return None
    global_lines.append(line)
    match = PRESENT.search(line)
    if match:
        gid, name, members = match.groups()
        item = guilds.setdefault(gid, {'name': name, 'members': members, 'lines': deque(maxlen=MAX_LINES), 'removed': False})
        item.update(name=name, members=members, removed=False)
        item['lines'].append(line)
        return gid
    match = REMOVED.search(line)
    if match:
        gid = match.group(1)
        if gid in guilds:
            guilds[gid]['removed'] = True
            guilds[gid]['lines'].append(line)
        return gid
    match = GUILD_ID.search(line)
    if match:
        gid = match.group(1)
        item = guilds.setdefault(gid, {'name': 'Guild ' + gid, 'members': '?', 'lines': deque(maxlen=MAX_LINES), 'removed': False})
        item['lines'].append(line)
        return gid
    # Legacy scheduler events report guild name instead of ID.
    if 'guild=' in line:
        name_match = re.search(r'\bguild=([^|]+)', line)
        if name_match:
            name = name_match.group(1).strip()
            matches = [g for g in guilds.values() if g['name'] == name]
            if len(matches) == 1:
                matches[0]['lines'].append(line)
    return None

