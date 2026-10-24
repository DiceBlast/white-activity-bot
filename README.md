# Activity Rewards

Windows self-hosted Discord activity and rewards bot.

## Fresh installation

1. Run `INSTALL.bat`.
2. Run `SETUP.bat`. It securely prompts for local credentials and prints the Discord invite URL.
3. Open the invite URL, add the bot to your server, then run `START_BOT.bat`.

## Updates

Use **Settings → Updates → Check GitHub Releases**. When a new verified release is available, select **Install Latest Release**. It downloads the release, makes a code-only backup, preserves all local bot data, and restarts automatically. Git is not required.

For zero-setup updates, publish releases from a **public** GitHub repository. Private releases require a GitHub token on each host, so they are not recommended for a friend’s 24/7 PC.

For an old extracted ZIP installation that has not been connected to Git history, run `CONNECT_GITHUB_UPDATES.bat` from this release and choose that older bot folder. It creates a separate local backup before attaching it to GitHub.

## Safe upgrade

1. Use **Shutdown All** on the existing bot.
2. Extract this release into a separate folder.
3. Run `UPGRADE_EXISTING.bat` and choose the existing bot folder.
4. Run `INSTALL.bat` in the existing bot folder if dependencies changed, then run `START_BOT.bat`.

The bot never ships or overwrites local tokens, points, databases, logs, backups, preferences, or the virtual environment.
