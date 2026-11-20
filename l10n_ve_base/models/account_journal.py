# Copyright 2026 BWEALTHICS LLC
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import fields, models

EMISSION_MEDIUM_SELECTION = [
    ("free", "Free form"),
    ("contingency", "Contingency"),
    ("fiscal_machine", "Fiscal machine"),
    ("digital", "Digital printing house"),
]
# The control number is pre-printed on the paper form by the authorized
# printing house, so the user types it before posting.
PREPRINTED_MEDIUMS = ("free", "contingency")
# The digital printing house or the fiscal machine assigns the control
# number once the document is issued; a connector writes it back.
POST_ASSIGNED_MEDIUMS = ("digital", "fiscal_machine")


class AccountJournal(models.Model):
    _inherit = "account.journal"

    l10n_ve_emission_medium = fields.Selection(
        selection=EMISSION_MEDIUM_SELECTION,
        string="Emission Medium",
        help="Medium used to issue the Venezuelan customer documents of "
        "this journal. It decides whether the control number is typed "
        "before posting or received afterwards.",
    )
