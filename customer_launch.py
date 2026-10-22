"""Launch original bot with customer-local credentials, no source modification."""
import json
import os
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parent
def main():
    config_path = ROOT / 'local_credentials.json'
    if not config_path.is_file():
        raise SystemExit('Missing local credentials. Run SETUP.bat first.')
    settings = json.loads(config_path.read_text(encoding='utf-8'))
    token = str(settings.get('bot_token', '')).strip()
    if not token:
        raise SystemExit('No bot token configured. Run SETUP.bat.')
    os.environ['DISCORD_BOT_TOKEN'] = token
    os.chdir(ROOT)
    runpy.run_path(str(ROOT / 'bot.py'), run_name='__main__')

if __name__ == '__main__':
    main()
