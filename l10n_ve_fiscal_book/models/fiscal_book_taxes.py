# Copyright 2011-2016 Vauxoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import api, fields, models


class FiscalBookTaxes(models.Model):
    _name = "fiscal.book.taxes"
    _description = "Fiscal Book Tax Summary"

    fb_id = fields.Many2one(
        comodel_name="fiscal.book", string="Fiscal Book", ondelete="cascade"
    )
    base_amount = fields.Float()
    tax_amount = fields.Float()
    appl_type = fields.Selection(
        [
            ("exento", "Exempt"),
            ("sdcf", "SDCF"),
            ("general", "General"),
            ("reducido", "Reduced"),
            ("adicional", "Additional"),
        ],
        string="Type",
    )

    @api.model_create_multi
    def create(self, vals_list):
        books = self.env["fiscal.book"].browse(
            [vals["fb_id"] for vals in vals_list if vals.get("fb_id")]
        )
        books._l10n_ve_check_locked_edit()
        return super().create(vals_list)

    def write(self, vals):
        books = self.fb_id
        if vals.get("fb_id"):
            # Moving a row into a posted book is also a change to that book.
            books |= self.env["fiscal.book"].browse(vals["fb_id"])
        books._l10n_ve_check_locked_edit()
        return super().write(vals)

    def unlink(self):
        self.fb_id._l10n_ve_check_locked_edit()
        return super().unlink()
