WhiteLabel Activity Bot v4.0.7

Windows package for a self-hosted Discord activity and rewards bot.

Fresh installation:
1. Run INSTALL.bat.
2. Run SETUP.bat to save the application ID and bot token locally.
3. Run INVITE_BOT.bat to invite your own Discord application.
4. Run CHECK_RELEASE.bat for a read-only local validation.
5. Run START_BOT.bat.

Safe upgrade:
1. Stop all bot processes using Shutdown All and back up the existing installation.
2. Extract this package into a separate folder, not over the existing installation.
3. Run UPGRADE_EXISTING.bat here and provide the existing bot folder when prompted.
4. Start the original installation with START_BOT.bat.

Do not copy data, backups, logs, .venv, tokens, or credentials between installations.
The upgrade tool replaces code only and creates a rollback archive before making changes.
