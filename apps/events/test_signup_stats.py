"""The signups table's Zwift Racing figures, from the rider profile cache.

The figures the table always had (vELO, its peaks and their categories, phenotype, age)
now come from ``rider_data.RiderProfile``, as do the 53 columns the column menu adds:
vELO 2 with its 30- and 90-day peaks, each with its open and (for a woman) women's category,
and six factors, seed ratings, phenotype scores, terrain handicaps, power and race counts
(``apps/events/rider_stats.py``). The added columns travel as JSON that the page builds cells
from, and only to those who see the full table.
"""

import json
import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from apps.events import rider_stats
from apps.events.models import Event, EventSignup


@pytest.fixture
def event(db):
    today = date.today()
    return Event.objects.create(
        title="ZRL",
        start_date=today,
        end_date=today + timedelta(days=30),
        visible=True,
        signups_open=True,
        show_signups=True,
    )


def _signup(user_model, event, username, zwid=None):
    user = user_model.objects.create_user(
        username=username, email=f"{username}@example.test", first_name=username.title(), last_name="R", zwid=zwid
    )
    return EventSignup.objects.create(event=event, user=user, status=EventSignup.Status.REGISTERED)


def _profile(rider_profile_factory, zwid):
    """Give an invented rider every Zwift Racing block the profile can carry.

    Returns:
        The profile.

    """
    return rider_profile_factory(
        zwid=zwid,
        velo=1523.6,
        category_racing="Diamond",
        ratings={
            "rating_max30": 1540.2,
            "rating_max90": 1611.0,
            "category_max30": "Diamond",
            "category_max90": "Ruby",
            "compound_score": 1234.0,
        },
        velo2={
            "race": 430.4,
            "race_max30": 450.0,
            "race_max90": 470.0,
            "category": "Amethyst",
            "category_max30": "Amethyst",
            "category_max90": "Sapphire",
            # Sent for every rider; shown for a woman only.
            "category_women": "Emerald",
            "category_women_max30": "Emerald",
            "category_women_max90": "Ruby",
            "factors": {
                "sprint": 452.0,
                "punch": 440.5,
                "climb": 401.0,
                "timeTrial": 415,
                "endurance": 398.0,
                "pursuit": 420.0,
            },
        },
        seed={"race": 421.0, "timeTrial": 380.6, "factors": {"sprint": 460, "timeTrial": 401}},
        handicaps={"flat": 1.26, "hilly": -0.4},
        power={
            "cp": 312.4,
            "awc": 21480.6,
            "curve_w": {"5": 1100, "1200": 310},
            "curve_wkg": {"5": 14.67, "1200": 4.13},
        },
        race_counts={"finishes": 88, "dnfs": 3, "wins": 5, "podiums": 14},
    )


def _page(client, event):
    return client.get(reverse("events:event_detail", args=[event.pk])).content.decode()


def _json(body, element_id):
    found = re.search(rf'<script id="{element_id}" type="application/json">(.*?)</script>', body, re.S)
    return json.loads(found.group(1)) if found else None


def _cell(body, signup, column):
    row = body[body.index(f'data-signup-pk="{signup.pk}"') :]
    cell = row[row.index(f'<td data-col="{column}"') :]
    return re.sub(r"<[^>]+>", "", cell[: cell.index("</td>")]).strip()


# --- the columns the table always had ------------------------------------------------------------


@pytest.mark.django_db
def test_the_table_reads_velo_from_the_profile(client, event, event_admin, user_model, rider_profile_factory):
    signup = _signup(user_model, event, "ana", zwid=4242)
    _profile(rider_profile_factory, 4242)
    client.force_login(event_admin)

    body = _page(client, event)

    assert _cell(body, signup, "zr_cur_rating") == "1524"  # a whole number, as zwiftracing shows it
    assert _cell(body, signup, "zr_cur_cat") == "Diamond"
    assert (_cell(body, signup, "zr_max30_rating"), _cell(body, signup, "zr_max30_cat")) == ("1540", "Diamond")
    assert (_cell(body, signup, "zr_max90_rating"), _cell(body, signup, "zr_max90_cat")) == ("1611", "Ruby")
    assert _cell(body, signup, "zr_phenotype") == "Sprinter"


@pytest.mark.django_db
def test_a_rider_without_a_profile_shows_no_figures(client, event, event_admin, user_model):
    signup = _signup(user_model, event, "bo", zwid=5151)
    client.force_login(event_admin)

    body = _page(client, event)

    assert _cell(body, signup, "zr_cur_rating") == "-"
    assert str(signup.pk) not in _json(body, "signup-stat-values")


def test_in_zwiftracing_follows_whether_zauth_holds_a_record(db, rider_profile_factory):
    held = rider_profile_factory(zwid=101)
    missing = rider_profile_factory(zwid=102, has_account={"zwift_api": True, "zwiftpower": True, "zwiftracing": False})

    assert rider_stats.zr_fields(held)["in_zwiftracing"] is True
    assert rider_stats.zr_fields(missing)["in_zwiftracing"] is False
    assert rider_stats.zr_fields(None)["zr_current_rating"] is None


# --- the columns the menu adds ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_column_menu_offers_every_group(client, event, event_admin, user_model):
    _signup(user_model, event, "ana", zwid=4242)
    client.force_login(event_admin)

    body = _page(client, event)

    for group in ("vELO 2", "Seed", "Phenotype scores", "Terrain handicaps", "Power", "Races"):
        assert f'<li class="menu-title text-xs pt-2 text-base-content/70">{group}</li>' in body, group
    assert body.count('class="checkbox checkbox-xs stat-col-toggle"') == len(rider_stats.STAT_COLUMNS) == 53
    columns = _json(body, "signup-stat-columns")
    assert [c["key"] for c in columns] == [c.key for c in rider_stats.STAT_COLUMNS]
    # The category columns sort as text and show a badge; every other column is a number.
    assert {c["key"] for c in columns if c["kind"] == "category"} == {
        "zr_v2_cat",
        "zr_v2_cat_w",
        "zr_v2_max30_cat",
        "zr_v2_max30_cat_w",
        "zr_v2_max90_cat",
        "zr_v2_max90_cat_w",
    }


@pytest.mark.django_db
def test_every_added_column_carries_the_riders_figure(client, event, event_admin, user_model, rider_profile_factory):
    signup = _signup(user_model, event, "ana", zwid=4242)
    _profile(rider_profile_factory, 4242)
    client.force_login(event_admin)

    body = _page(client, event)
    keys = [c["key"] for c in _json(body, "signup-stat-columns")]
    row = dict(zip(keys, _json(body, "signup-stat-values")[str(signup.pk)], strict=True))

    # vELO 2, its peaks, the category of each, and its six factors.
    assert [row[f"zr_v2_{k}"] for k in ("race", "max30", "max90")] == ["430", "450", "470"]
    assert [row[f"zr_v2_{k}"] for k in ("cat", "max30_cat", "max90_cat")] == ["Amethyst", "Amethyst", "Sapphire"]
    # The factory's rider is a man, so the women's categories zwiftracing sent stay blank.
    assert [row[f"zr_v2_{k}_w"] for k in ("cat", "max30_cat", "max90_cat")] == ["", "", ""]
    factors = ("sprint", "punch", "climb", "time_trial", "endurance", "pursuit")
    assert [row[f"zr_v2_{k}"] for k in factors] == ["452", "441", "401", "415", "398", "420"]  # halves round up
    # The seed: its race and time-trial ratings, and the factors it has.
    assert (row["zr_seed_race"], row["zr_seed_tt"], row["zr_seed_sprint"]) == ("421", "381", "460")
    assert (row["zr_seed_tt_factor"], row["zr_seed_punch"]) == ("401", "")
    # Phenotype scores and terrain handicaps, blank where the profile has none.
    # The factory's phenotype scores: sprinter 80, climber 40.
    assert (row["zr_ph_sprinter"], row["zr_ph_climber"], row["zr_ph_puncheur"]) == ("80.0", "40.0", "")
    assert (row["zr_hc_flat"], row["zr_hc_hilly"], row["zr_hc_rolling"]) == ("1.3", "-0.4", "")
    # Power: compound score, CP, W′ in kJ, and the curve in W/kg and in watts.
    assert (row["zr_compound"], row["zr_cp"], row["zr_wprime"]) == ("1234", "312", "21.5")
    assert (row["zr_wkg_5"], row["zr_wkg_1200"], row["zr_wkg_60"]) == ("14.67", "4.13", "")
    assert (row["zr_w_5"], row["zr_w_1200"]) == ("1100", "310")
    # Race counts.
    assert [row[f"zr_{k}"] for k in ("finishes", "wins", "podiums", "dnfs")] == ["88", "5", "14", "3"]


@pytest.mark.django_db
def test_a_names_only_viewer_gets_none_of_the_figures(client, event, team_member, user_model, rider_profile_factory):
    """With show_signups on, any team member sees the names; the figures stay with the full table."""
    _signup(user_model, event, "ana", zwid=4242)
    _profile(rider_profile_factory, 4242)
    client.force_login(team_member)

    body = _page(client, event)

    assert "Ana" in body  # the names-only list
    assert _json(body, "signup-stat-values") is None
    assert "stat-col-toggle" not in body


# --- the women's vELO 2 categories ------------------------------------------------------------------


@pytest.mark.django_db
def test_a_womans_row_carries_her_womens_velo2_categories(
    client, event, event_admin, user_model, rider_profile_factory
):
    """Every rider gets a women's category from zwiftracing; the table shows a woman's only."""
    woman = _signup(user_model, event, "ana", zwid=4242)
    woman.user.gender = "female"
    woman.user.save(update_fields=["gender"])
    man = _signup(user_model, event, "bo", zwid=5151)
    _profile(rider_profile_factory, 4242)
    _profile(rider_profile_factory, 5151)
    client.force_login(event_admin)

    body = _page(client, event)
    keys = [c["key"] for c in _json(body, "signup-stat-columns")]
    values = _json(body, "signup-stat-values")
    hers = dict(zip(keys, values[str(woman.pk)], strict=True))
    his = dict(zip(keys, values[str(man.pk)], strict=True))

    women_columns = ("zr_v2_cat_w", "zr_v2_max30_cat_w", "zr_v2_max90_cat_w")
    assert [hers[k] for k in women_columns] == ["Emerald", "Emerald", "Ruby"]
    assert [his[k] for k in women_columns] == ["", "", ""]
    # Beside each ZR category -- vELO 1's mixed one -- her vELO 2 women's one, named in full.
    assert " ".join(_cell(body, woman, "zr_cur_cat").split()) == "Diamond vELO 2 women's category Emerald W Emerald"
    assert " ".join(_cell(body, woman, "zr_max90_cat").split()) == "Ruby vELO 2 women's category Ruby W Ruby"
    assert _cell(body, man, "zr_cur_cat") == "Diamond"


def test_zr_fields_gives_a_woman_her_womens_categories_and_nobody_else(db, rider_profile_factory):
    profile = rider_profile_factory(
        zwid=101, gender="", velo2={"race": 430.0, "category_women": "Emerald", "category_women_max30": "Ruby"}
    )

    hers = rider_stats.zr_fields(profile, gender="female")
    assert (hers["zr_v2_women_category"], hers["zr_v2_women_max30_category"]) == ("Emerald", "Ruby")
    assert hers["zr_v2_women_max90_category"] == ""
    assert rider_stats.zr_fields(profile, gender="male")["zr_v2_women_category"] == ""
    assert rider_stats.zr_fields(None, gender="female")["zr_v2_women_category"] == ""
    # Every key the rider popover is handed exists, profile or not.
    assert set(rider_stats.TOOLTIP_FIELDS) <= set(rider_stats.zr_fields(None)) & set(hers)
