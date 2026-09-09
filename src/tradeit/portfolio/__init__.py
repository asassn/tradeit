"""Portfolio state, position sizing, allocation and the cycle that joins them.

``base`` holds the contracts; the rest implement them. ``sizing`` expresses
size in risk rather than dollars, ``allocation`` ranks a basket rather than the
names in it, ``stops`` runs the ladder a stop climbs, and ``cycle`` manages what
is held before deploying what that frees.

Compounding has no module of its own, and should not get one: it is what the
cycle does when sizes are fractions of current equity and exits are planned
before entries.
"""
