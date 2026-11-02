"""Seat arithmetic of the Knesset and the counterfactual seat estimate of the article.

`allocation`     exact Bader–Ofer allocation (highest averages with surplus-vote agreements, 3.25 % threshold),
                 built on the implementation verified against the official 2022 allocation in `battery/t16_representation.py`.
`coverage`       the sets of localities and stations where the article's premise is applied (coverage points A and B).
`counterfactual` the transfer of votes that removes the posited effect δ from Likud in the covered units.
`estimate`       the grid coverage × δ × variant, the Monte-Carlo over δ, provenance and tables.
`figures`        the article's figures (matplotlib, project theme).
`article`        the Markdown article rendered from its template with every number taken from the results.

Nothing here alleges wrongdoing: the module computes how many seats a posited vote shift would have moved under the
seat-allocation law, as a function of where the shift is applied and how large it is.
"""

from electionforensics.seats.allocation import Allocation, allocate_totals, totals_from_stations

__all__ = ["Allocation", "allocate_totals", "totals_from_stations"]
