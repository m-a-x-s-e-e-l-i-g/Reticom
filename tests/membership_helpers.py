import RNS

from retium.membership import Membership


def approve_test_members(receiver, directory, *members):
    """Give focused transport fixtures an explicit, real signed allowlist."""
    receiver.identity = RNS.Identity()
    receiver.membership = Membership(directory, receiver.identity)
    for member in members:
        key = member if isinstance(member, str) else member.hash.hex()
        receiver.membership.decide(key, "approved", "Test member")
