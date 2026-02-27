"""Find orphaned squad members with no active event signup."""

from apps.events.models import EventSignup, SquadMember

orphans = [
    sm
    for sm in SquadMember.objects.filter(status="member").select_related("squad__event", "user")
    if not EventSignup.objects.filter(event=sm.squad.event, user=sm.user, status="registered").exists()
]

if orphans:
    print(f"Found {len(orphans)} orphaned squad member(s):")
    for sm in orphans:
        print(f"  - {sm.user} in squad \"{sm.squad.name}\" (event: \"{sm.squad.event.title}\")")
else:
    print("No orphaned squad members found.")
