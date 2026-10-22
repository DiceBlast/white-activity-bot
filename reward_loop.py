from discord.ext import tasks

from config_manager import load_config
from database import acknowledge_notification, claim_available_rewards, pending_notifications
from messages import reward_message
from logger import get_logger

log = get_logger('activitybot.rewards')
from voice import flush_all


async def run_reward_iteration(bot):
    flush_all()
    for guild in bot.guilds:
        try:
            config = load_config(guild.id)
            claim_available_rewards(guild.id, config)
            channel_id = int(config['channels'].get('reward_channel', 0))
            if not channel_id:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            for user in pending_notifications(guild.id):
                normal_points = int(user.get('pending_normal_points', 0))
                event_points = int(user.get('pending_event_points', 0))
                if normal_points + event_points <= 0:
                    continue
                member = guild.get_member(int(user['discord_id']))
                if member is None:
                    continue
                try:
                    await channel.send(reward_message(
                        member, normal_points, event_points,
                        int(user.get('score', 0)), config,
                    ))
                except Exception:
                    # Never acknowledge a notification that was not sent.
                    log.exception('Reward notification failed | guild_id=%s user_id=%s',
                                  guild.id, user['discord_id'])
                    continue
                try:
                    acknowledge_notification(guild.id, user['discord_id'], normal_points, event_points)
                except Exception:
                    # Delivery succeeded; retry could duplicate the message. Log explicitly.
                    log.exception('Reward acknowledgement failed; possible duplicate on retry | guild_id=%s user_id=%s',
                                  guild.id, user['discord_id'])
                    continue
                log.info('Reward sent | guild_id=%s user_id=%s normal=%s event=%s total=%s',
                         guild.id, user['discord_id'], normal_points, event_points,
                         int(user.get('score', 0)))
        except Exception:
            log.exception('Reward processing failed for guild | guild_id=%s', guild.id)


def start_reward_loop(bot):
    existing = getattr(bot, 'reward_loop_task', None)
    if existing is not None and existing.is_running():
        return existing

    @tasks.loop(minutes=1, reconnect=True)
    async def reward_loop():
        await run_reward_iteration(bot)

    @reward_loop.before_loop
    async def before_reward_loop():
        await bot.wait_until_ready()

    @reward_loop.error
    async def reward_loop_error(error):
        log.error('Reward loop error: %s', error, exc_info=(type(error), error, error.__traceback__))

    bot.reward_loop_task = reward_loop
    reward_loop.start()
    return reward_loop
