# Copyright 2011-2016 Vauxoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import api, fields, models


class FiscalBookLine(models.Model):
    _name = "fiscal.book.line"
    _description = "Fiscal Book Line"
    _order = "rank asc"

    fb_id = fields.Many2one(
        comodel_name="fiscal.book",
        string="Fiscal Book",
        ondelete="cascade",
        required=True,
    )
    rank = fields.Integer(string="Operation N°", default=1)
    move_id = fields.Many2one(
        comodel_name="account.move", string="Invoice / Move", required=True
    )
    partner_id = fields.Many2one(comodel_name="res.partner")
    partner_vat = fields.Char(string="RIF / CI")
    doc_date = fields.Date()
    invoice_number = fields.Char()
    nro_ctrl = fields.Char(string="Control Number")
    affected_number = fields.Char(
        string="Affected Document",
        help="Original invoice number for credit/debit notes.",
    )
    doc_type = fields.Selection(
        [
            ("01", "Invoice"),
            ("02", "Debit Note"),
            ("03", "Credit Note"),
            ("04", "VAT Adjustment"),
            ("05", "Import (F86)"),
        ],
        default="01",
    )
    expediente = fields.Char()
    customs_number = fields.Char(string="Form 86")
    is_adjustment = fields.Boolean(string="VAT Adjustment")
    withholding_only = fields.Boolean(
        string="Withholding in this period",
        help="Invoice belongs to another month; only the VAT withholding of "
        "this period is shown.",
    )
    total_amount = fields.Float()
    exempt_amount = fields.Float()
    sdcf_amount = fields.Float(string="SDCF Amount")
    base_general = fields.Float()
    tax_general = fields.Float()
    base_reduced = fields.Float()
    tax_reduced = fields.Float()
    base_additional = fields.Float()
    tax_additional = fields.Float()
    vat_withheld = fields.Float(string="VAT Withheld")
    voucher_number = fields.Char(string="Withholding Voucher")

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
