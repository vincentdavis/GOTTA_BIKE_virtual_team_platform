"""The ratchet that keeps the retention policy honest.

"Most tables have no retention policy at all" was true when this was written, and the reason it
stayed true is that nothing made it visible. A model with no rule looks exactly like a model
whose rule is "keep" -- both simply never delete anything -- so the gap could not be seen from
the code, and a document listing the gap would have drifted out of date the way ``TODO.md``
did.

These tests remove the third state. Every model either declares a policy or is named in
``UNCLASSIFIED`` below. Adding a model without a decision fails the suite; classifying one and
forgetting to strike it off the list also fails. The list can therefore only ever shrink, and
its length is an honest measure of how far the work has got.

``UNCLASSIFIED`` is not a to-do list in priority order -- see the retention classes for that.
It is only the set of models nobody has decided about yet.
"""

import pytest
from django.apps import apps as django_apps

from gotta_bike_platform.retention import RetentionPolicy, policy_for

# Models with no retention decision yet. THIS LIST MAY ONLY SHRINK -- classify a model, then
# delete its line. Do not add to it; a new model should arrive with a policy.
#
# The two Historical* entries are here deliberately rather than hidden. django-simple-history
# generates them, they hold roughly thirty times the rows of the tables they shadow, and their
# policy resolves from their subject -- so classifying ZPTeamRiders or ZRRider will make the
# ratchet demand these lines go too, which is the moment to remember that a sweep over the live
# table does not touch its history.
UNCLASSIFIED = {
    "accounts.BlockedDiscordId",
    "accounts.GuildMember",
    "accounts.User",
    "accounts.YouTubeVideo",
    "analytics.PageVisit",
    "data_connection.DataConnection",
    "dbot_api.BotStats",
    "events.AvailabilityGrid",
    "events.AvailabilityGridTemplate",
    "events.AvailabilityResponse",
    "events.AvailabilitySlotSelection",
    "events.Event",
    "events.EventSignup",
    "events.Race",
    "events.RaceRegistration",
    "events.SignupQuestion",
    "events.SlotDS",
    "events.Squad",
    "events.SquadMember",
    "ladder_planner.CachedClub",
    "ladder_planner.CachedRider",
    "ladder_planner.LadderMatchup",
    "ladder_planner.LadderRider",
    "magic_links.MagicLink",
    "team.MembershipApplication",
    "team.RaceReadyRecord",
    "team.RecordView",
    "team.RosterFilter",
    "tickets.Ticket",
    "ttt_planner.PlanRider",
    "ttt_planner.TttPlan",
    "user_api.UserApiKey",
    "zwiftpower.HistoricalZPTeamRiders",
    "zwiftpower.ZPEvent",
    "zwiftpower.ZPRiderResults",
    "zwiftpower.ZPTeamRiders",
    "zwiftracing.HistoricalZRRider",
    "zwiftracing.ZRRider",
}


def _project_models():
    """Every model this project owns, excluding Django's own and third-party apps.

    Returns:
        A list of model classes.

    """
    ours = {
        config.label
        for config in django_apps.get_app_configs()
        if config.name.startswith("apps.") or config.name == "gotta_bike_platform"
    }
    return [model for model in django_apps.get_models() if model._meta.app_label in ours]


def test_every_model_is_either_classified_or_listed_as_unclassified():
    """No model may quietly default to keeping personal data forever."""
    undeclared = {m._meta.label for m in _project_models() if policy_for(m) is None}
    missing = undeclared - UNCLASSIFIED
    assert not missing, (
        f"These models have no retention policy and are not listed as unclassified: "
        f"{sorted(missing)}. Declare `retention = RetentionPolicy...` on the model, or add it "
        f"to UNCLASSIFIED with the intention of coming back to it."
    )


def test_the_unclassified_list_contains_nothing_already_classified():
    """The ratchet: once a model is classified its line must go, so the list only shrinks."""
    stale = {label for label in UNCLASSIFIED if policy_for(django_apps.get_model(label)) is not None}
    assert not stale, f"Now classified — remove from UNCLASSIFIED: {sorted(stale)}"


def test_the_unclassified_list_names_only_real_models():
    """A renamed or deleted model must not leave a line behind that silently excuses nothing."""
    real = {m._meta.label for m in _project_models()}
    assert not (UNCLASSIFIED - real), f"No such model: {sorted(UNCLASSIFIED - real)}"


def test_every_declared_policy_explains_itself():
    """A bare "keep" is indistinguishable from never having decided; the reason is the decision."""
    for model in _project_models():
        policy = policy_for(model)
        if policy is not None:
            assert policy.reason.strip(), f"{model._meta.label} declares a policy with no reason"
            assert len(policy.reason) > 30, f"{model._meta.label}'s reason is too terse to be useful"


def test_timed_policies_carry_an_anchor_and_a_setting():
    """A window is meaningless without a field to measure from and a place to configure it."""
    for model in _project_models():
        policy = policy_for(model)
        if policy is None or policy.kind in (RetentionPolicy.KIND_KEEP, RetentionPolicy.KIND_CASCADE):
            continue
        assert policy.anchor, f"{model._meta.label}: {policy.kind} needs an anchor field"
        assert policy.setting, f"{model._meta.label}: {policy.kind} needs a Constance setting"


def test_untimed_policies_carry_no_anchor():
    """'keep' and 'cascade' have no clock, so an anchor on one signals a copy-paste mistake."""
    for model in _project_models():
        policy = policy_for(model)
        if policy and policy.kind in (RetentionPolicy.KIND_KEEP, RetentionPolicy.KIND_CASCADE):
            assert policy.anchor is None, f"{model._meta.label}: {policy.kind} should not have an anchor"


def test_a_history_model_resolves_to_its_subjects_policy(monkeypatch):
    """Shadow tables cannot declare for themselves, so classifying the subject must cover them.

    Asserted through the mechanism rather than against a live pair, so the test keeps working as
    models are classified.
    """
    from apps.zwiftracing.models import ZRRider

    history_model = ZRRider.history.model
    declared = RetentionPolicy.keep("Stand-in policy used only to prove resolution works here.")
    monkeypatch.setattr(ZRRider, "retention", declared, raising=False)

    assert policy_for(history_model) is declared


def test_the_reference_data_class_is_declared():
    """The first class to be classified: reference and configuration, kept deliberately."""
    expected = {
        "zwift_data.ZwiftWorld",
        "zwift_data.ZwiftRoute",
        "zwift_data.ZwiftSegment",
        "zwift_data.ZwiftDataset",
        "ttt_planner.PowerUp",
        "team.DiscordRole",
        "team.DiscordChannel",
        "team.TeamLink",
        "cms.Page",
        "gotta_bike_platform.SiteSettings",
    }
    for label in expected:
        policy = policy_for(django_apps.get_model(label))
        assert policy is not None, f"{label} should be classified as keep"
        assert policy.kind == RetentionPolicy.KIND_KEEP, f"{label} should be keep, not {policy.kind}"


@pytest.mark.parametrize("label", sorted(UNCLASSIFIED))
def test_unclassified_models_are_reported_individually(label):
    """Renders the outstanding work as one skipped test each, so progress is visible in a run."""
    pytest.skip(f"{label} has no retention policy yet")
