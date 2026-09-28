from app.agent.timefmt import alert_window, clock_time, short_time, weekday_time

# Mon 2026-09-28 13:59:52 EDT
NOW = 1790618392


def test_clock_time_is_new_york_local():
    assert clock_time(NOW) == "1:59 PM"
    assert clock_time(NOW + 11 * 3600) == "12:59 AM"
    assert weekday_time(NOW) == "Mon 1:59 PM"


def test_short_time_drops_zero_minutes_and_adds_weekday_off_today():
    five_am_sun = 1791104400  # Sun 2026-10-04 05:00 EDT
    assert short_time(five_am_sun, NOW) == "5 AM Sun"
    assert short_time(NOW + 1800 + 8, NOW) == "2:30 PM"


def test_alert_window():
    assert alert_window(NOW - 60, 1791104400, NOW) == "now until 5 AM Sun"
    assert alert_window(NOW - 60, None, NOW) == "now, until further notice"
    assert alert_window(NOW + 1808, NOW + 5408, NOW) == "starts 2:30 PM until 3:30 PM"
