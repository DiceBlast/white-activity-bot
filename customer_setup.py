"""Local interactive configuration. Never ship local_credentials.json to customers."""
import getpass
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
CREDS = ROOT / 'local_credentials.json'

def main():
    print('=== Activity Rewards: first-time self-hosted setup ===')
    print('Create YOUR OWN application at https://discord.com/developers/applications')
    print('In its Bot tab, create/copy the bot token. Do NOT use a shared token.')
    print('Enable Server Members Intent in the Bot tab if your server tracking needs it.')
    print('IMPORTANT: Your token will be stored locally in plain text; keep this folder private.')
    if CREDS.exists():
        answer = input('Existing local credentials found. Replace them? [y/N]: ').strip().lower()
        if answer != 'y':
            print('Existing credentials left unchanged.')
            return
    app_id = input('Enter your Application ID (numbers only): ').strip()
    if not re.fullmatch(r'[0-9]{15,22}', app_id):
        raise SystemExit('Invalid Application ID. No files were changed.')
    token = getpass.getpass('Paste your BOT TOKEN (hidden; not your application ID): ').strip()
    if not token or len(token) < 25 or any(c.isspace() for c in token):
        raise SystemExit('Invalid-looking token. No files were changed.')
    data = {'application_id': app_id, 'bot_token': token}
    tmp = CREDS.with_suffix('.json.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CREDS)
    finally:
        tmp.unlink(missing_ok=True)
    print('Local settings saved. No token has been printed.')
    print('Next: run INVITE_BOT.bat and then START_BOT.bat.')

if __name__ == '__main__':
    main()
