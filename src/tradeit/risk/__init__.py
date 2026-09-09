"""Portfolio risk limits and the veto gate before an order exists.

``base`` holds the contracts. ``rules`` implements the limits that read only a
portfolio snapshot, ``contextual`` those needing evidence a snapshot cannot
carry -- sectors, correlations, a peak-equity mark -- and ``engine`` aggregates
whichever are installed by taking the most restrictive answer.

Every rule here fails closed. Missing evidence is a rejection, not an
allowance, because a limit fed nothing otherwise reports comfortable numbers
and constrains nothing.
"""
