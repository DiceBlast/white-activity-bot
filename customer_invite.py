"""Open an installation URL for the customer's OWN Discord bot application."""
import json
from pathlib import Path
import re
import urllib.parse
import webbrowser

ROOT = Path(__file__).resolve().parent
def make_invite_url(app_id):
    if not re.fullmatch(r'[0-9]{15,22}', str(app_id)):
        raise ValueError('Invalid application ID')
    return 'https://discord.com/oauth2/authorize?' + urllib.parse.urlencode({
        'client_id': app_id,
        'scope': 'bot applications.commands',
        # Invite with no elevated permission bits by default. Grant required channel
        # permissions through Discord's role/channel settings after inviting.
        'permissions': '0',
    })

def main():
    config_path = ROOT / 'local_credentials.json'
    if not config_path.exists():
        raise SystemExit('Run SETUP.bat first to configure your bot.')
    settings = json.loads(config_path.read_text(encoding='utf-8'))
    url = make_invite_url(settings['application_id'])
    print('Opening your Discord bot invite link (not the seller bot).')
    print('Note: You must grant the bot channel access and permissions for its features.')
    print(url)
    if not webbrowser.open(url):
        print('Browser did not open. Copy the link above into your browser.')

if __name__ == '__main__':
    main()
