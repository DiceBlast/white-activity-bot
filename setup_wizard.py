import discord

from config_manager import (
    DEFAULT_CONFIG,
    load_config,
    mark_setup_section,
    save_config,
    setup_status,
    validate_day,
    validate_time,
)
from database import leaderboard as get_leaderboard, monthly_winners
from messages import event_end, event_reminder, event_start, first_bonus, monthly_winner_message, reward_message, weekly_message


def _yes_no(value: str) -> bool:
    value = str(value).strip().lower()
    if value in {'yes', 'y', 'true', '1', 'on', 'enable', 'enabled'}:
        return True
    if value in {'no', 'n', 'false', '0', 'off', 'disable', 'disabled'}:
        return False
    raise ValueError('Use yes/no for enabled settings.')


def _section_line(config, key, label):
    done = bool(config.get('setup', {}).get('sections', {}).get(key, False))
    return f"{'✅' if done else '❌'} {label}"


def _summary(config: dict) -> str:
    b = config['branding']
    r = config['rewards']
    e = config['event']
    v = config['voice']
    w = config['weekly_announcement']
    m = config['monthly']
    c = config['channels']
    status = setup_status(config)

    reward_channel = f"<#{c['reward_channel']}>" if int(c.get('reward_channel', 0)) else 'Not set'
    announcement_channel = f"<#{c['announcement_channel']}>" if int(c.get('announcement_channel', 0)) else 'Not set'
    admin_role = f"<@&{config['admin_role_id']}>" if int(config.get('admin_role_id', 0)) else 'Manage Server / Administrator'
    excluded_count = len(v.get('excluded_channel_ids', []))

    checklist = '\n'.join([
        _section_line(config, 'branding', 'Branding'),
        _section_line(config, 'rewards', 'Rewards'),
        _section_line(config, 'event', 'Event'),
        _section_line(config, 'voice', 'Voice rules'),
        _section_line(config, 'weekly', 'Weekly leaderboard'),
        _section_line(config, 'monthly', 'Monthly competition'),
        _section_line(config, 'channels', 'Channels'),
    ])

    return (
        f"🤖 **{b['bot_nickname']} Setup**\n"
        f"📋 Setup progress: **{status['done']}/{status['total']}**\n{checklist}\n\n"
        f"🏷️ Currency: {b['currency_emoji']} **{b['currency_name']}**\n"
        f"⏱️ Reward: **+{r['points_per_interval']} every {r['interval_minutes']} min**\n"
        f"🎁 First voice bonus: **{r['first_join_bonus']}** ({'ON' if r['first_join_bonus_enabled'] else 'OFF'})\n\n"
        f"🔥 Event: **{b['event_name']} x{e['multiplier']}** ({'ON' if e['enabled'] else 'OFF'})\n"
        f"📅 {e['start_day']} {e['start_time']} → {e['end_day']} {e['end_time']}\n"
        f"⏰ Reminder: **{'ON' if e.get('reminder_enabled', True) else 'OFF'}**, {e.get('reminder_minutes_before', 60)} min before\n"
        f"🏁 End announcement: **{'ON' if e.get('end_announcement_enabled', True) else 'OFF'}**\n\n"
        f"🎙️ Voice minimum members: **{v.get('minimum_members_in_channel', 1)}**\n"
        f"🚫 Excluded voice channels: **{excluded_count}**\n"
        f"🏆 Weekly: **{'ON' if w.get('enabled', True) else 'OFF'}** — {w.get('day')} {w.get('time')}\n"
        f"👑 Monthly: **{'ON' if m['enabled'] else 'OFF'}** — Top **{m.get('winner_count', 1)}**\n"
        f"🥇 1st: **{m.get('prizes', {}).get('1', '')}**\n"
        + (f"🥈 2nd: **{m.get('prizes', {}).get('2', '')}**\n🥉 3rd: **{m.get('prizes', {}).get('3', '')}**\n" if int(m.get('winner_count', 1)) == 3 else '')
        + "\n"
        f"💬 Reward channel: {reward_channel}\n"
        f"📢 Announcement channel: {announcement_channel}\n"
        f"🛡️ Admin role: {admin_role}\n"
        f"🌍 Timezone: **{config['timezone']}**\n"
        f"💾 Daily backups: **{'ON' if config['backups']['enabled'] else 'OFF'}** at {config['backups']['hour']:02d}:00"
    )


def _save_section(guild_id, config, section):
    mark_setup_section(config, section)
    return save_config(guild_id, config)


class BrandingModal(discord.ui.Modal, title='Branding Setup'):
    nickname = discord.ui.TextInput(label='Bot nickname', max_length=32)
    currency = discord.ui.TextInput(label='Currency name', max_length=32)
    emoji = discord.ui.TextInput(label='Currency emoji', max_length=16)
    event_name = discord.ui.TextInput(label='Event name', max_length=64)
    leaderboard_name = discord.ui.TextInput(label='Leaderboard name', max_length=64)

    def __init__(self, guild_id):
        super().__init__(); self.guild_id = guild_id
        b = load_config(guild_id)['branding']
        self.nickname.default = b['bot_nickname']; self.currency.default = b['currency_name']; self.emoji.default = b['currency_emoji']; self.event_name.default = b['event_name']; self.leaderboard_name.default = b['leaderboard_name']

    async def on_submit(self, interaction):
        config = load_config(self.guild_id); b = config['branding']
        b['bot_nickname'] = str(self.nickname.value).strip()[:32] or b['bot_nickname']
        b['currency_name'] = str(self.currency.value).strip()[:32] or b['currency_name']
        b['currency_emoji'] = str(self.emoji.value).strip()[:16] or b['currency_emoji']
        b['event_name'] = str(self.event_name.value).strip()[:64] or b['event_name']
        b['leaderboard_name'] = str(self.leaderboard_name.value).strip()[:64] or b['leaderboard_name']
        _save_section(self.guild_id, config, 'branding')
        if interaction.guild and interaction.guild.me:
            try: await interaction.guild.me.edit(nick=b['bot_nickname'])
            except discord.Forbidden: pass
        await interaction.response.send_message('✅ Branding saved. Use **Refresh Summary** in `/setup`.', ephemeral=True)


class RewardsModal(discord.ui.Modal, title='Rewards Setup'):
    interval = discord.ui.TextInput(label='Reward interval (minutes)', max_length=6)
    points = discord.ui.TextInput(label='Points per interval', max_length=8)
    first_bonus = discord.ui.TextInput(label='First voice bonus (0 = off)', max_length=8)

    def __init__(self, guild_id):
        super().__init__(); self.guild_id = guild_id
        r = load_config(guild_id)['rewards']
        self.interval.default = str(r['interval_minutes']); self.points.default = str(r['points_per_interval']); self.first_bonus.default = str(r['first_join_bonus'])

    async def on_submit(self, interaction):
        try:
            interval = max(1, min(1440, int(self.interval.value))); points = max(1, min(100000, int(self.points.value))); bonus = max(0, min(100000, int(self.first_bonus.value)))
        except ValueError:
            await interaction.response.send_message('❌ Reward values must be whole numbers.', ephemeral=True); return
        config = load_config(self.guild_id); r = config['rewards']
        r.update({'interval_minutes': interval, 'points_per_interval': points, 'first_join_bonus': bonus, 'first_join_bonus_enabled': bonus > 0})
        _save_section(self.guild_id, config, 'rewards')
        await interaction.response.send_message('✅ Reward settings saved.', ephemeral=True)


class EventScheduleModal(discord.ui.Modal, title='Event Schedule & Multiplier'):
    multiplier = discord.ui.TextInput(label='Multiplier', max_length=4)
    start_day = discord.ui.TextInput(label='Start day', max_length=10)
    start_time = discord.ui.TextInput(label='Start time (HH:MM)', max_length=5)
    end_day = discord.ui.TextInput(label='End day', max_length=10)
    end_time = discord.ui.TextInput(label='End time (HH:MM)', max_length=5)

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id
        event = load_config(guild_id)['event']
        self.multiplier.default = str(event['multiplier'])
        self.start_day.default = event['start_day']
        self.start_time.default = event['start_time']
        self.end_day.default = event['end_day']
        self.end_time.default = event['end_time']

    async def on_submit(self, interaction):
        try:
            multiplier = max(1, min(20, int(self.multiplier.value)))
            start_day = validate_day(self.start_day.value)
            start_time = validate_time(self.start_time.value)
            end_day = validate_day(self.end_day.value)
            end_time = validate_time(self.end_time.value)
        except ValueError as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return
        config = load_config(self.guild_id)
        config['event'].update({
            'multiplier': multiplier,
            'start_day': start_day,
            'start_time': start_time,
            'end_day': end_day,
            'end_time': end_time,
        })
        _save_section(self.guild_id, config, 'event')
        await interaction.response.send_message('✅ Event schedule saved.', ephemeral=True)


class EventEnabledModal(discord.ui.Modal, title='Event On / Off'):
    enabled = discord.ui.TextInput(label='Event enabled? yes/no', max_length=8)

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id
        self.enabled.default = 'yes' if load_config(guild_id)['event']['enabled'] else 'no'

    async def on_submit(self, interaction):
        try:
            enabled = _yes_no(self.enabled.value)
        except ValueError as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return
        config = load_config(self.guild_id)
        config['event']['enabled'] = enabled
        _save_section(self.guild_id, config, 'event')
        await interaction.response.send_message(f'✅ Event {"enabled" if enabled else "disabled"}.', ephemeral=True)


class EventAnnouncementsModal(discord.ui.Modal, title='Event Announcements'):
    reminder_enabled = discord.ui.TextInput(label='Start reminder enabled? yes/no', max_length=8)
    reminder_minutes = discord.ui.TextInput(label='Minutes before start', max_length=6)
    end_enabled = discord.ui.TextInput(label='End announcement enabled? yes/no', max_length=8)

    def __init__(self, guild_id):
        super().__init__(); self.guild_id = guild_id; e = load_config(guild_id)['event']
        self.reminder_enabled.default = 'yes' if e.get('reminder_enabled', True) else 'no'; self.reminder_minutes.default = str(e.get('reminder_minutes_before', 60)); self.end_enabled.default = 'yes' if e.get('end_announcement_enabled', True) else 'no'

    async def on_submit(self, interaction):
        try:
            reminder = _yes_no(self.reminder_enabled.value); minutes = max(1, min(10080, int(self.reminder_minutes.value))); end = _yes_no(self.end_enabled.value)
        except ValueError as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True); return
        config = load_config(self.guild_id); config['event'].update({'reminder_enabled': reminder, 'reminder_minutes_before': minutes, 'end_announcement_enabled': end})
        _save_section(self.guild_id, config, 'event')
        await interaction.response.send_message('✅ Event announcements saved.', ephemeral=True)


class VoiceRulesModal(discord.ui.Modal, title='Voice Rules'):
    minimum = discord.ui.TextInput(label='Minimum human members in voice', max_length=2)
    ignore_afk = discord.ui.TextInput(label='Ignore AFK channel? yes/no', max_length=8)
    ignore_deaf = discord.ui.TextInput(label='Ignore deafened users? yes/no', max_length=8)

    def __init__(self, guild_id):
        super().__init__(); self.guild_id = guild_id; v = load_config(guild_id)['voice']
        self.minimum.default = str(v.get('minimum_members_in_channel', 1)); self.ignore_afk.default = 'yes' if v.get('ignore_afk_channel', True) else 'no'; self.ignore_deaf.default = 'yes' if v.get('ignore_deafened_users', True) else 'no'

    async def on_submit(self, interaction):
        try:
            minimum = max(1, min(99, int(self.minimum.value))); ignore_afk = _yes_no(self.ignore_afk.value); ignore_deaf = _yes_no(self.ignore_deaf.value)
        except ValueError as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True); return
        config = load_config(self.guild_id); config['voice'].update({'minimum_members_in_channel': minimum, 'ignore_afk_channel': ignore_afk, 'ignore_deafened_users': ignore_deaf})
        _save_section(self.guild_id, config, 'voice')
        await interaction.response.send_message('✅ Voice rules saved.', ephemeral=True)


class WeeklyModal(discord.ui.Modal, title='Weekly Leaderboard Setup'):
    enabled = discord.ui.TextInput(label='Enabled? yes/no', max_length=8)
    day = discord.ui.TextInput(label='Day', max_length=10)
    time = discord.ui.TextInput(label='Time (HH:MM)', max_length=5)

    def __init__(self, guild_id):
        super().__init__(); self.guild_id = guild_id; w = load_config(guild_id)['weekly_announcement']
        self.enabled.default = 'yes' if w.get('enabled', True) else 'no'; self.day.default = w['day']; self.time.default = w['time']

    async def on_submit(self, interaction):
        try: enabled = _yes_no(self.enabled.value); day = validate_day(self.day.value); time = validate_time(self.time.value)
        except ValueError as exc: await interaction.response.send_message(f'❌ {exc}', ephemeral=True); return
        config = load_config(self.guild_id); config['weekly_announcement'].update({'enabled': enabled, 'day': day, 'time': time}); _save_section(self.guild_id, config, 'weekly')
        await interaction.response.send_message('✅ Weekly settings saved.', ephemeral=True)


class MonthlySettingsModal(discord.ui.Modal, title='Monthly Competition Settings'):
    enabled = discord.ui.TextInput(label='Enabled? yes/no', max_length=8)
    winner_count = discord.ui.TextInput(label='Winner count (1 or 3)', max_length=1)
    title_text = discord.ui.TextInput(label='Monthly title', max_length=64)

    def __init__(self, guild_id):
        super().__init__(); self.guild_id = guild_id; c = load_config(guild_id)
        self.enabled.default = 'yes' if c['monthly']['enabled'] else 'no'
        self.winner_count.default = str(c['monthly'].get('winner_count', 1))
        self.title_text.default = c['branding']['monthly_title']

    async def on_submit(self, interaction):
        try:
            enabled = _yes_no(self.enabled.value)
            winner_count = int(str(self.winner_count.value).strip())
            if winner_count not in (1, 3):
                raise ValueError('Winner count must be 1 or 3.')
        except ValueError as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True); return
        config = load_config(self.guild_id)
        config['monthly']['enabled'] = enabled
        config['monthly']['winner_count'] = winner_count
        config['branding']['monthly_title'] = str(self.title_text.value).strip()[:64] or 'Monthly Champion'
        _save_section(self.guild_id, config, 'monthly')
        await interaction.response.send_message(f'✅ Monthly settings saved. Using **Top {winner_count}**.', ephemeral=True)


class MonthlyPrizesModal(discord.ui.Modal, title='Monthly Prizes'):
    first = discord.ui.TextInput(label='1st place prize', style=discord.TextStyle.paragraph, max_length=300)
    second = discord.ui.TextInput(label='2nd place prize', style=discord.TextStyle.paragraph, max_length=300, required=False)
    third = discord.ui.TextInput(label='3rd place prize', style=discord.TextStyle.paragraph, max_length=300, required=False)

    def __init__(self, guild_id):
        super().__init__(); self.guild_id = guild_id; prizes = load_config(guild_id)['monthly'].get('prizes', {})
        self.first.default = prizes.get('1', 'Custom Prize')
        self.second.default = prizes.get('2', 'Second Place Prize')
        self.third.default = prizes.get('3', 'Third Place Prize')

    async def on_submit(self, interaction):
        config = load_config(self.guild_id)
        prizes = config['monthly'].setdefault('prizes', {})
        prizes['1'] = str(self.first.value).strip()[:300] or 'Custom Prize'
        prizes['2'] = str(self.second.value).strip()[:300] or 'Second Place Prize'
        prizes['3'] = str(self.third.value).strip()[:300] or 'Third Place Prize'
        _save_section(self.guild_id, config, 'monthly')
        await interaction.response.send_message('✅ Monthly prizes saved.', ephemeral=True)


class MonthlyMenuView(discord.ui.View):
    def __init__(self, guild_id, owner_id):
        super().__init__(timeout=300); self.guild_id = guild_id; self.owner_id = owner_id
    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('❌ Only the person who opened `/setup` can use this panel.', ephemeral=True); return False
        return True
    @discord.ui.button(label='Settings', style=discord.ButtonStyle.primary, emoji='⚙️')
    async def settings(self, interaction, button): await interaction.response.send_modal(MonthlySettingsModal(self.guild_id))
    @discord.ui.button(label='Prizes', style=discord.ButtonStyle.secondary, emoji='🎁')
    async def prizes(self, interaction, button): await interaction.response.send_modal(MonthlyPrizesModal(self.guild_id))


class EventTemplateModal(discord.ui.Modal, title='Event Message Templates'):
    reminder = discord.ui.TextInput(label='Reminder template', style=discord.TextStyle.paragraph, max_length=1800)
    start = discord.ui.TextInput(label='Start template', style=discord.TextStyle.paragraph, max_length=1800)
    end = discord.ui.TextInput(label='End template', style=discord.TextStyle.paragraph, max_length=1800)
    def __init__(self, guild_id):
        super().__init__(); self.guild_id = guild_id; t = load_config(guild_id)['templates']; self.reminder.default=t['event_reminder']; self.start.default=t['event_start']; self.end.default=t['event_end']
    async def on_submit(self, interaction):
        c=load_config(self.guild_id); c['templates']['event_reminder']=str(self.reminder.value); c['templates']['event_start']=str(self.start.value); c['templates']['event_end']=str(self.end.value); save_config(self.guild_id,c); await interaction.response.send_message('✅ Event templates saved. Placeholders are preserved exactly as typed.', ephemeral=True)


class RewardTemplateModal(discord.ui.Modal, title='Reward Message Templates'):
    first = discord.ui.TextInput(label='First bonus template', style=discord.TextStyle.paragraph, max_length=1800)
    normal = discord.ui.TextInput(label='Normal reward template', style=discord.TextStyle.paragraph, max_length=1800)
    event = discord.ui.TextInput(label='Event reward template', style=discord.TextStyle.paragraph, max_length=1800)
    mixed = discord.ui.TextInput(label='Mixed reward template', style=discord.TextStyle.paragraph, max_length=1800)
    def __init__(self, guild_id):
        super().__init__(); self.guild_id=guild_id; t=load_config(guild_id)['templates']; self.first.default=t['first_bonus']; self.normal.default=t['reward_normal']; self.event.default=t['reward_event']; self.mixed.default=t['reward_mixed']
    async def on_submit(self, interaction):
        c=load_config(self.guild_id); t=c['templates']; t['first_bonus']=str(self.first.value); t['reward_normal']=str(self.normal.value); t['reward_event']=str(self.event.value); t['reward_mixed']=str(self.mixed.value); save_config(self.guild_id,c); await interaction.response.send_message('✅ Reward templates saved.', ephemeral=True)


class CompetitionTemplateModal(discord.ui.Modal, title='Competition Templates'):
    weekly = discord.ui.TextInput(label='Weekly template', style=discord.TextStyle.paragraph, max_length=1800)
    monthly = discord.ui.TextInput(label='Monthly winner template', style=discord.TextStyle.paragraph, max_length=1800)
    def __init__(self, guild_id):
        super().__init__(); self.guild_id=guild_id; t=load_config(guild_id)['templates']; self.weekly.default=t['weekly']; self.monthly.default=t['monthly_winner']
    async def on_submit(self, interaction):
        c=load_config(self.guild_id); c['templates']['weekly']=str(self.weekly.value); c['templates']['monthly_winner']=str(self.monthly.value); save_config(self.guild_id,c); await interaction.response.send_message('✅ Competition templates saved.', ephemeral=True)


class VoiceChannelView(discord.ui.View):
    def __init__(self, guild_id, owner_id):
        super().__init__(timeout=300); self.guild_id=guild_id; self.owner_id=owner_id; self.selected=None
    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id: await interaction.response.send_message('❌ Only the person who opened `/setup` can use this panel.', ephemeral=True); return False
        return True
    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.voice, discord.ChannelType.stage_voice], placeholder='Select voice channels to exclude', min_values=0, max_values=10)
    async def channels(self, interaction, select):
        self.selected=[x.id for x in select.values]; await interaction.response.defer()
    @discord.ui.button(label='Save Excluded Channels', style=discord.ButtonStyle.success, emoji='💾')
    async def save(self, interaction, button):
        c=load_config(self.guild_id); c['voice']['excluded_channel_ids']=self.selected or []; _save_section(self.guild_id,c,'voice'); await interaction.response.edit_message(content=f'✅ Saved **{len(c["voice"]["excluded_channel_ids"])}** excluded voice channel(s).', view=None)
    @discord.ui.button(label='Clear All', style=discord.ButtonStyle.danger, emoji='🧹')
    async def clear(self, interaction, button):
        c=load_config(self.guild_id); c['voice']['excluded_channel_ids']=[]; _save_section(self.guild_id,c,'voice'); await interaction.response.edit_message(content='✅ Excluded voice channels cleared.', view=None)


class ChannelAdminView(discord.ui.View):
    def __init__(self, guild_id, owner_id):
        super().__init__(timeout=300); self.guild_id=guild_id; self.owner_id=owner_id; self.reward_channel_id=None; self.announcement_channel_id=None; self.admin_role_id=None
    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id: await interaction.response.send_message('❌ Only the person who opened `/setup` can use this panel.', ephemeral=True); return False
        return True
    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder='Choose reward channel', min_values=1, max_values=1)
    async def reward_channel(self, interaction, select): self.reward_channel_id=select.values[0].id; await interaction.response.defer()
    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder='Choose announcement channel', min_values=1, max_values=1)
    async def announcement_channel(self, interaction, select): self.announcement_channel_id=select.values[0].id; await interaction.response.defer()
    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder='Optional admin role', min_values=0, max_values=1)
    async def admin_role(self, interaction, select): self.admin_role_id=select.values[0].id if select.values else 0; await interaction.response.defer()
    @discord.ui.button(label='Save Channels & Admin', style=discord.ButtonStyle.success, emoji='💾')
    async def save(self, interaction, button):
        c=load_config(self.guild_id)
        if self.reward_channel_id is not None: c['channels']['reward_channel']=self.reward_channel_id
        if self.announcement_channel_id is not None: c['channels']['announcement_channel']=self.announcement_channel_id
        if self.admin_role_id is not None: c['admin_role_id']=self.admin_role_id
        if int(c['channels'].get('reward_channel',0)) and int(c['channels'].get('announcement_channel',0)): mark_setup_section(c,'channels')
        save_config(self.guild_id,c)
        await interaction.response.edit_message(content='✅ Channels/admin settings saved.', view=None)


class EventMenuView(discord.ui.View):
    def __init__(self,guild_id,owner_id): super().__init__(timeout=300); self.guild_id=guild_id; self.owner_id=owner_id
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id: await interaction.response.send_message('❌ Only the person who opened `/setup` can use this panel.',ephemeral=True); return False
        return True
    @discord.ui.button(label='Schedule & Multiplier',style=discord.ButtonStyle.primary,emoji='📅')
    async def schedule(self,interaction,button): await interaction.response.send_modal(EventScheduleModal(self.guild_id))
    @discord.ui.button(label='Enable / Disable Event',style=discord.ButtonStyle.secondary,emoji='🔥')
    async def toggle_event(self,interaction,button): await interaction.response.send_modal(EventEnabledModal(self.guild_id))
    @discord.ui.button(label='Announcements',style=discord.ButtonStyle.secondary,emoji='📢')
    async def announcements(self,interaction,button): await interaction.response.send_modal(EventAnnouncementsModal(self.guild_id))


class VoiceMenuView(discord.ui.View):
    def __init__(self,guild_id,owner_id): super().__init__(timeout=300); self.guild_id=guild_id; self.owner_id=owner_id
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id: await interaction.response.send_message('❌ Only the person who opened `/setup` can use this panel.',ephemeral=True); return False
        return True
    @discord.ui.button(label='Voice Rules',style=discord.ButtonStyle.primary,emoji='🎙️')
    async def rules(self,interaction,button): await interaction.response.send_modal(VoiceRulesModal(self.guild_id))
    @discord.ui.button(label='Excluded Channels',style=discord.ButtonStyle.secondary,emoji='🚫')
    async def excluded(self,interaction,button): await interaction.response.send_message('Select channels where members should **not** earn rewards.',view=VoiceChannelView(self.guild_id,self.owner_id),ephemeral=True)


class TemplateMenuView(discord.ui.View):
    def __init__(self,guild_id,owner_id): super().__init__(timeout=300); self.guild_id=guild_id; self.owner_id=owner_id
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id: await interaction.response.send_message('❌ Only the person who opened `/setup` can use this panel.',ephemeral=True); return False
        return True
    @discord.ui.button(label='Reward Messages',style=discord.ButtonStyle.primary,emoji='⭐')
    async def reward(self,interaction,button): await interaction.response.send_modal(RewardTemplateModal(self.guild_id))
    @discord.ui.button(label='Event Messages',style=discord.ButtonStyle.primary,emoji='🔥')
    async def event(self,interaction,button): await interaction.response.send_modal(EventTemplateModal(self.guild_id))
    @discord.ui.button(label='Weekly / Monthly',style=discord.ButtonStyle.secondary,emoji='🏆')
    async def competitions(self,interaction,button): await interaction.response.send_modal(CompetitionTemplateModal(self.guild_id))


class TestToolsView(discord.ui.View):
    def __init__(self,guild_id,owner_id): super().__init__(timeout=300); self.guild_id=guild_id; self.owner_id=owner_id
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id: await interaction.response.send_message('❌ Only the person who opened `/setup` can use this panel.',ephemeral=True); return False
        return True
    async def _announcement(self,interaction,message):
        c=load_config(self.guild_id); channel=interaction.guild.get_channel(int(c['channels'].get('announcement_channel',0)))
        if not channel: await interaction.response.send_message('❌ Configure the announcement channel first.',ephemeral=True); return
        await channel.send('🧪 **TEST PREVIEW**\n\n'+message); await interaction.response.send_message(f'✅ Test sent to {channel.mention}.',ephemeral=True)
    @discord.ui.button(label='Event Reminder',style=discord.ButtonStyle.secondary)
    async def reminder(self,interaction,button): c=load_config(self.guild_id); await self._announcement(interaction,event_reminder(c,c['event'].get('reminder_minutes_before',60)))
    @discord.ui.button(label='Event Start',style=discord.ButtonStyle.secondary)
    async def start(self,interaction,button): await self._announcement(interaction,event_start(load_config(self.guild_id)))
    @discord.ui.button(label='Event End',style=discord.ButtonStyle.secondary)
    async def end(self,interaction,button): await self._announcement(interaction,event_end(load_config(self.guild_id)))
    @discord.ui.button(label='Weekly',style=discord.ButtonStyle.secondary)
    async def weekly(self,interaction,button):
        c=load_config(self.guild_id); rows=get_leaderboard(self.guild_id,c); medals=['🥇','🥈','🥉']; lines=[]
        for i,u in enumerate(rows): lines.append(f"{medals[i] if i<3 else str(i+1)+'.'} {u['name']} — {u['score']} {c['branding']['currency_name']}")
        await self._announcement(interaction,weekly_message(c,'\n'.join(lines) if lines else 'No scores yet.'))
    @discord.ui.button(label='Reward',style=discord.ButtonStyle.secondary)
    async def reward(self,interaction,button):
        c=load_config(self.guild_id); channel=interaction.guild.get_channel(int(c['channels'].get('reward_channel',0)))
        if not channel: await interaction.response.send_message('❌ Configure the reward channel first.',ephemeral=True); return
        await channel.send('🧪 **TEST PREVIEW**\n\n'+reward_message(interaction.user,c['rewards']['points_per_interval'],0,123,c)); await interaction.response.send_message(f'✅ Test sent to {channel.mention}.',ephemeral=True)
    @discord.ui.button(label='Monthly Winner',style=discord.ButtonStyle.secondary,row=1)
    async def monthly(self,interaction,button):
        c=load_config(self.guild_id); winners=monthly_winners(self.guild_id,c); count=int(c['monthly'].get('winner_count',1));
        if not winners:
            examples=[{'name':'Example Winner','score':100},{'name':'Example Runner-Up','score':80},{'name':'Example Third','score':60}]
            winners=examples[:count]
        await self._announcement(interaction,monthly_winner_message(c,winners))


class ResetConfirmView(discord.ui.View):
    def __init__(self,guild_id,owner_id): super().__init__(timeout=60); self.guild_id=guild_id; self.owner_id=owner_id
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id: await interaction.response.send_message('❌ Only the person who opened `/setup` can use this panel.',ephemeral=True); return False
        return True
    @discord.ui.button(label='Yes, Reset Configuration',style=discord.ButtonStyle.danger)
    async def confirm(self,interaction,button):
        save_config(self.guild_id, DEFAULT_CONFIG); await interaction.response.edit_message(content='✅ Configuration reset to defaults. User scores were **not** deleted.',view=None)
    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.secondary)
    async def cancel(self,interaction,button): await interaction.response.edit_message(content='Reset cancelled.',view=None)


class SetupView(discord.ui.View):
    def __init__(self,guild_id,owner_id): super().__init__(timeout=900); self.guild_id=guild_id; self.owner_id=owner_id
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id: await interaction.response.send_message('❌ Only the person who opened `/setup` can use this panel.',ephemeral=True); return False
        return True
    @discord.ui.button(label='Branding',style=discord.ButtonStyle.primary,emoji='🏷️',row=0)
    async def branding(self,interaction,button): await interaction.response.send_modal(BrandingModal(self.guild_id))
    @discord.ui.button(label='Rewards',style=discord.ButtonStyle.primary,emoji='⭐',row=0)
    async def rewards(self,interaction,button): await interaction.response.send_modal(RewardsModal(self.guild_id))
    @discord.ui.button(label='Event',style=discord.ButtonStyle.primary,emoji='🔥',row=0)
    async def event(self,interaction,button): await interaction.response.send_message('🔥 **Event Setup**',view=EventMenuView(self.guild_id,self.owner_id),ephemeral=True)
    @discord.ui.button(label='Voice',style=discord.ButtonStyle.primary,emoji='🎙️',row=0)
    async def voice(self,interaction,button): await interaction.response.send_message('🎙️ **Voice Rules**',view=VoiceMenuView(self.guild_id,self.owner_id),ephemeral=True)
    @discord.ui.button(label='Weekly',style=discord.ButtonStyle.secondary,emoji='🏆',row=1)
    async def weekly(self,interaction,button): await interaction.response.send_modal(WeeklyModal(self.guild_id))
    @discord.ui.button(label='Monthly',style=discord.ButtonStyle.secondary,emoji='👑',row=1)
    async def monthly(self,interaction,button): await interaction.response.send_message('👑 **Monthly Competition**\nChoose winner count/settings or configure each position prize.',view=MonthlyMenuView(self.guild_id,self.owner_id),ephemeral=True)
    @discord.ui.button(label='Channels & Admin',style=discord.ButtonStyle.secondary,emoji='📢',row=1)
    async def channels(self,interaction,button): await interaction.response.send_message('📢 **Channels & Admin**\nChoose reward/announcement channels. Admin role is optional.',view=ChannelAdminView(self.guild_id,self.owner_id),ephemeral=True)
    @discord.ui.button(label='Message Templates',style=discord.ButtonStyle.secondary,emoji='✏️',row=1)
    async def templates(self,interaction,button): await interaction.response.send_message('✏️ **Custom Message Templates**\nAvailable placeholders include `{mention}`, `{amount}`, `{currency_name}`, `{event_name}`, `{multiplier}`, `{ranking}`, `{winner_name}`, `{winner_score}`, `{prize_text}`, and `{winners}`.',view=TemplateMenuView(self.guild_id,self.owner_id),ephemeral=True)
    @discord.ui.button(label='Test Tools',style=discord.ButtonStyle.secondary,emoji='🧪',row=2)
    async def tests(self,interaction,button): await interaction.response.send_message('🧪 These previews do not change scores or scheduler state.',view=TestToolsView(self.guild_id,self.owner_id),ephemeral=True)
    @discord.ui.button(label='Refresh Summary',style=discord.ButtonStyle.secondary,emoji='🔄',row=2)
    async def refresh(self,interaction,button): await interaction.response.edit_message(content=_summary(load_config(self.guild_id))+'\n\nUse the buttons below to configure everything.',view=self)
    @discord.ui.button(label='Finish Setup',style=discord.ButtonStyle.success,emoji='✅',row=2)
    async def finish(self,interaction,button):
        c=load_config(self.guild_id); status=setup_status(c)
        if not status['complete']:
            missing=', '.join(x.title() for x in status['missing']); await interaction.response.send_message(f'❌ Setup is not complete yet. Missing: **{missing}**.',ephemeral=True); return
        c['setup']['completed']=True; save_config(self.guild_id,c); await interaction.response.edit_message(content='✅ **Setup complete!**\n\n'+_summary(c),view=None)
    @discord.ui.button(label='Reset Defaults',style=discord.ButtonStyle.danger,emoji='⚠️',row=2)
    async def reset(self,interaction,button): await interaction.response.send_message('⚠️ Reset bot configuration to defaults? **Scores will not be deleted.**',view=ResetConfirmView(self.guild_id,self.owner_id),ephemeral=True)


def setup_panel(guild_id:int,owner_id:int):
    c=load_config(guild_id); return _summary(c), SetupView(guild_id,owner_id)
