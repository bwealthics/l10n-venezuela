# Copyright 2011-2016 Vauxoo
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .account_journal import (
    EMISSION_MEDIUM_SELECTION,
    POST_ASSIGNED_MEDIUMS,
    PREPRINTED_MEDIUMS,
)


class AccountMove(models.Model):
    _inherit = "account.move"

    nro_ctrl = fields.Char(
        string="Control Number",
        copy=False,
        tracking=True,
        help="Pre-printed invoice control number required by SENIAT",
    )
    sin_cred = fields.Boolean(
        string="Exclude from Fiscal Book",
        copy=False,
        help="Set to true if invoice is exempt or excluded from Venezuelan fiscal book",
    )
    date_document = fields.Date(
        string="Document Date",
        copy=False,
        help="Administrative date printed on the physical vendor bill for fiscal books",
    )
    invoice_printer = fields.Char(
        string="Fiscal Printer Invoice Number",
        copy=False,
        help="Invoice number emitted by the fiscal printer",
    )
    fiscal_printer = fields.Char(
        string="Fiscal Printer Serial",
        copy=False,
        help="Serial number of the fiscal printer",
    )
    z_report = fields.Char(
        string="Report Z",
        copy=False,
        help="Z Report number emitted by fiscal printer",
    )
    supplier_invoice_number = fields.Char(
        copy=False,
        help="Original invoice number from supplier",
    )
    l10n_ve_control_date = fields.Date(
        string="Control Number Date",
        copy=False,
        tracking=True,
        help="Date on which the printing house or the fiscal machine "
        "assigned the control number.",
    )
    l10n_ve_emission_medium = fields.Selection(
        selection=EMISSION_MEDIUM_SELECTION,
        string="Emission Medium",
        compute="_compute_l10n_ve_emission_medium",
        store=True,
        copy=False,
        help="Emission medium of the sales journal, kept on the document "
        "when it is posted so later journal changes do not alter it.",
    )

    @api.depends("journal_id", "move_type")
    def _compute_l10n_ve_emission_medium(self):
        for move in self:
            move.l10n_ve_emission_medium = (
                move._l10n_ve_is_emission_document()
                and move.journal_id.l10n_ve_emission_medium
            )

    @api.constrains("nro_ctrl", "partner_id", "move_type", "company_id")
    def _check_unique_nro_ctrl(self):
        for move in self:
            if not (move.is_invoice() and move.nro_ctrl):
                continue
            nro = move.nro_ctrl.strip()
            domain = [
                ("id", "!=", move.id),
                ("company_id", "=", move.company_id.id),
                ("nro_ctrl", "=", nro),
                ("state", "!=", "cancel"),
            ]
            # One control number series covers invoices, credit and debit
            # notes of the issuer: the company for sales, each supplier
            # for purchases.
            if move.is_sale_document(include_receipts=True):
                domain.append(
                    ("move_type", "in", self.get_sale_types(include_receipts=True))
                )
            else:
                domain += [
                    (
                        "move_type",
                        "in",
                        self.get_purchase_types(include_receipts=True),
                    ),
                    # Core books payables and payments on the commercial
                    # partner; the invoice contact is billing data only, so
                    # contacts of one supplier are the same issuer.
                    (
                        "commercial_partner_id",
                        "=",
                        move.commercial_partner_id.id,
                    ),
                ]
            if self.search_count(domain):
                raise ValidationError(
                    self.env._(
                        "The Control Number %s has already been "
                        "registered for this company.",
                        nro,
                    )
                )

    @api.onchange("invoice_date")
    def _onchange_invoice_date_ve(self):
        if self.invoice_date and not self.date_document:
            self.date_document = self.invoice_date

    def _l10n_ve_is_fiscal_invoice(self):
        self.ensure_one()
        return self.is_invoice(include_receipts=True)

    def _l10n_ve_is_emission_document(self):
        """Customer document of a Venezuelan company: it has a medium."""
        self.ensure_one()
        return self.country_code == "VE" and self.is_sale_document(
            include_receipts=True
        )

    def _l10n_ve_check_emission_medium(self):
        """Snapshot the journal medium and apply its posting rule.

        Pre-printed media carry the control number on the paper, so it is
        required before posting. Digital printing houses and fiscal
        machines assign it after issuing; see
        ``_l10n_ve_assign_control_data``.
        """
        self.ensure_one()
        if not self._l10n_ve_is_emission_document():
            return
        medium = self.journal_id.l10n_ve_emission_medium
        if self.l10n_ve_emission_medium != medium:
            self.l10n_ve_emission_medium = medium
        if medium in PREPRINTED_MEDIUMS and not (self.nro_ctrl or "").strip():
            raise UserError(
                self.env._(
                    "%s is issued on a pre-printed form: type its Control "
                    "Number before posting.",
                    self.display_name,
                )
            )

    def _l10n_ve_assign_control_data(self, control_number, control_date=None):
        """Receive the control data assigned after posting.

        Entry point for digital printing house and fiscal machine
        connectors. The number is written once on a posted document and is
        never replaced; repeating the same number is a no-op so connectors
        can retry. The uniqueness constraint still applies.
        """
        self.ensure_one()
        control_number = (control_number or "").strip()
        if not control_number:
            raise UserError(self.env._("A Control Number is required."))
        if (
            self.state != "posted"
            or not self._l10n_ve_is_emission_document()
            or self.l10n_ve_emission_medium not in POST_ASSIGNED_MEDIUMS
        ):
            raise UserError(
                self.env._(
                    "Control data can only be received on posted Venezuelan "
                    "customer documents issued by a digital printing house "
                    "or a fiscal machine."
                )
            )
        if self.nro_ctrl == control_number:
            # Connector retry: already received, nothing to write.
            return True
        if self.nro_ctrl:
            raise UserError(
                self.env._(
                    "%(document)s already has Control Number %(number)s.",
                    document=self.display_name,
                    number=self.nro_ctrl,
                )
            )
        self.with_context(l10n_ve_allow_nro_ctrl=True).write(
            {
                "nro_ctrl": control_number,
                "l10n_ve_control_date": control_date or fields.Date.context_today(self),
            }
        )
        return True

    def _l10n_ve_taxable_untaxed(self):
        """Untaxed amount excluding IGTF expense lines on vendor bills."""
        self.ensure_one()
        extra = sum(
            self.invoice_line_ids.filtered(
                lambda line: getattr(line, "l10n_ve_igtf_line", False)
            ).mapped("price_subtotal")
        )
        return self.amount_untaxed - extra

    def _l10n_ve_validate_invoice(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        company_ve = self.company_id.account_fiscal_country_id.code == "VE" or (
            self.company_id.country_id and self.company_id.country_id.code == "VE"
        )
        partner_ve = partner.country_id and partner.country_id.code == "VE"
        if company_ve or partner_ve:
            if not partner.vat:
                raise UserError(
                    self.env._(
                        "Partner %s needs a RIF before posting "
                        "a Venezuelan fiscal invoice.",
                        partner.display_name,
                    )
                )
            if not partner._l10n_ve_is_valid_fiscal_id(partner.vat):
                raise UserError(
                    self.env._(
                        "The identification of %s is not valid "
                        "for a Venezuelan fiscal invoice.",
                        partner.display_name,
                    )
                )
        if abs(self.amount_total) < 0.01:
            raise UserError(
                self.env._(
                    "Invoice %s cannot be posted with a total of zero.",
                    self.display_name,
                )
            )
        if (
            self.invoice_date
            and self.invoice_date_due
            and self.invoice_date_due < self.invoice_date
        ):
            raise UserError(
                self.env._(
                    "The due date of %s cannot be earlier than the invoice date.",
                    self.display_name,
                )
            )
        if self.move_type in ("out_refund", "in_refund") and not self.reversed_entry_id:
            raise UserError(
                self.env._(
                    "Credit note %s must be linked to the original "
                    "invoice (reversed entry).",
                    self.display_name,
                )
            )
        for line in self.invoice_line_ids.filtered(
            lambda rec: rec.display_type == "product"
        ):
            if line.quantity < 0 or line.price_unit < 0:
                raise UserError(
                    self.env._(
                        "Invoice lines cannot have a negative quantity "
                        "or unit price (%s).",
                        line.display_name,
                    )
                )
            if len(line.tax_ids) > 1:
                raise UserError(
                    self.env._(
                        "Line '%s' cannot have more than one tax. "
                        "Split it into separate lines.",
                        line.name or line.display_name,
                    )
                )

    def _post(self, soft=True):
        for move in self:
            if not move._l10n_ve_is_fiscal_invoice():
                continue
            move._l10n_ve_validate_invoice()
            move._l10n_ve_check_emission_medium()
            if not move.date_document:
                move.date_document = move.invoice_date or fields.Date.context_today(
                    self
                )
            if (
                move.move_type in ("in_invoice", "in_refund")
                and not (move.nro_ctrl or "").strip()
            ):
                raise UserError(
                    self.env._(
                        "Vendor bill %s requires a Control Number "
                        "(nro. de control) before posting.",
                        move.display_name,
                    )
                )
        return super()._post(soft=soft)

    def write(self, vals):
        locked_fields = {
            "nro_ctrl",
            "date_document",
            "supplier_invoice_number",
            "l10n_ve_control_date",
            "l10n_ve_emission_medium",
        }
        if locked_fields & set(vals) and not self.env.context.get(
            "l10n_ve_allow_nro_ctrl"
        ):
            posted = self.filtered(
                lambda move: move.state == "posted"
                and move._l10n_ve_is_fiscal_invoice()
            )
            if posted:
                raise UserError(
                    self.env._(
                        "Fiscal identification fields cannot be changed "
                        "on posted invoices. Use a credit/debit note or "
                        "the control-number wizard on empty values."
                    )
                )
        return super().write(vals)
