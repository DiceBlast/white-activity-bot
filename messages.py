class _SafeDict(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def _context(config, **extra):
    b = config['branding']
    e = config['event']
    data = {
        'currency_name': b['currency_name'],
        'currency_emoji': b['currency_emoji'],
        'event_name': b['event_name'],
        'leaderboard_name': b['leaderboard_name'],
        'monthly_title': b['monthly_title'],
        'multiplier': e['multiplier'],
        'start_day': e['start_day'],
        'start_time': e['start_time'],
        'end_day': e['end_day'],
        'end_time': e['end_time'],
        'prize_text': config['monthly'].get('prizes', {}).get('1', 'Custom Prize'),
    }
    data.update(extra)
    return data


def render_template(config, key, **extra):
    template = config.get('templates', {}).get(key, '')
    try:
        return str(template).format_map(_SafeDict(_context(config, **extra)))
    except Exception:
        return str(template)


def first_bonus(member, points, config):
    return render_template(config, 'first_bonus', mention=member.mention, amount=points)


def reward_message(member, normal_points, event_points, total_score, config):
    total = normal_points + event_points
    payload = dict(
        mention=member.mention,
        amount=total,
        normal_points=normal_points,
        event_points=event_points,
        total_score=total_score,
    )
    if event_points and not normal_points:
        return render_template(config, 'reward_event', **payload)
    if event_points and normal_points:
        return render_template(config, 'reward_mixed', **payload)
    return render_template(config, 'reward_normal', **payload)


def event_reminder(config, reminder_minutes):
    return render_template(config, 'event_reminder', reminder_minutes=int(reminder_minutes))


def event_start(config):
    return render_template(config, 'event_start')


def event_end(config):
    return render_template(config, 'event_end')


def weekly_message(config, ranking):
    return render_template(config, 'weekly', ranking=ranking)


def monthly_winner_message(config, winners):
    if isinstance(winners, dict):
        winners = [winners]
    medals = ['🥇', '🥈', '🥉']
    prizes = config['monthly'].get('prizes', {})
    lines = []
    for index, winner in enumerate(winners[:3], start=1):
        medal = medals[index - 1]
        prize = str(prizes.get(str(index), '')).strip()
        lines.append(f"{medal} **{winner['name']}** — **{winner['score']} {config['branding']['currency_name']}**")
        if prize:
            lines.append(f"🎁 {prize}")
        if index < len(winners[:3]):
            lines.append('')
    winners_text = '\n'.join(lines) if lines else 'No eligible winners.'
    template = str(config.get('templates', {}).get('monthly_winner', ''))
    message = render_template(
        config,
        'monthly_winner',
        winners=winners_text,
        winner_name=winners[0]['name'] if winners else '',
        winner_score=winners[0]['score'] if winners else 0,
        prize_text=prizes.get('1', ''),
    )
    # Older custom templates may not have the new {winners} placeholder.
    if len(winners) > 1 and '{winners}' not in template:
        message = message.rstrip() + '\n\n' + winners_text
    return message

