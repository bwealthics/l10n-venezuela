# Copyright 2026 BWEALTHICS LLC
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import calendar
import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

VOUCHER_NUMBER_RE = re.compile(r"^\d{4}(0[1-9]|1[0-2])\d{8}$")

# Fiscal fields frozen once the voucher leaves draft (art. 16, PA
# SNAT/2025/000054): only the module's own transitions may change them.
# Set only by the module's own flows, never by a client, even in draft.
L10N_VE_SYSTEM_FIELDS = frozenset({"wh_move_id", "cancel_refund_id", "adjustment_ids"})

L10N_VE_LOCKED_FIELDS = frozenset(
    {
        "number",
        "date",
        "partner_id",
        "company_id",
        "base_amount",
        "tax_amount",
        "withheld_amount",
        "exempt_amount",
        "wh_rate",
        "move_ids",
        "wh_move_id",
        "cancel_refund_id",
        "state",
    }
)


class L10nVeIvaWhVoucher(models.Model):
    _name = "l10n.ve.iva.wh.voucher"
    _description = "Comprobante de Retención de IVA"
    _order = "date desc, number desc, id desc"
    _rec_name = "number"
    _check_company_auto = True

    number = fields.Char(
        string="Número de Comprobante",
        copy=False,
        readonly=True,
        index=True,
        help="Numeración consecutiva de 14 caracteres: AAAAMM + secuencial "
        "de 8 dígitos (art. 16, PA SNAT/2025/000054).",
    )
    date = fields.Date(
        string="Fecha de Emisión",
        required=True,
        default=fields.Date.context_today,
    )
    fiscal_period = fields.Char(
        string="Período Fiscal",
        compute="_compute_fiscal_period",
        help="Período de imposición en formato AAAAMM.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Compañía",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Sujeto Retenido",
        required=True,
        check_company=True,
    )
    wh_move_id = fields.Many2one(
        comodel_name="account.move",
        string="Asiento de Retención",
        copy=False,
        readonly=True,
        check_company=True,
        help="Asiento que traslada el IVA retenido de la cuenta por pagar del "
        "proveedor a Retenciones de IVA por Enterar, conciliado con el "
        "documento retenido.",
    )
    move_ids = fields.Many2many(
        comodel_name="account.move",
        string="Documentos Retenidos",
        check_company=True,
    )
    adjustment_ids = fields.One2many(
        comodel_name="l10n.ve.iva.wh.voucher.adjustment",
        inverse_name="voucher_id",
        string="Ajustes por Notas de Crédito",
        readonly=True,
    )
    cancel_refund_id = fields.Many2one(
        comodel_name="account.move",
        string="Anulado por Nota de Crédito",
        copy=False,
        readonly=True,
        check_company=True,
        help="Nota de crédito total, del mismo período de retención, que "
        "anuló este comprobante.",
    )
    base_amount = fields.Monetary(string="Base Imponible")
    tax_amount = fields.Monetary(string="Impuesto Causado (IVA)")
    withheld_amount = fields.Monetary(string="Impuesto Retenido")
    exempt_amount = fields.Monetary(string="Monto Exento")
    wh_rate = fields.Float(string="Porcentaje de Retención (%)")
    state = fields.Selection(
        selection=[
            ("draft", "Borrador"),
            ("posted", "Emitido"),
            ("cancel", "Anulado"),
        ],
        string="Estado",
        default="draft",
        copy=False,
        required=True,
    )

    # Issued vouchers only: the ones received from customers live on
    # account.payment, so the legal number is unique per withholding agent.
    _number_company_uniq = models.Constraint(
        "UNIQUE(company_id, number)",
        "El número de comprobante de retención debe ser único por compañía.",
    )

    @api.depends("date")
    def _compute_fiscal_period(self):
        for voucher in self:
            voucher.fiscal_period = (
                voucher.date and voucher.date.strftime("%Y%m") or False
            )

    @api.constrains("base_amount", "tax_amount", "withheld_amount", "exempt_amount")
    def _check_amounts_not_negative(self):
        # Credit notes are reported as positive type 03 lines: the tax
        # portal subtracts them, so a voucher never carries negative amounts.
        for voucher in self:
            amounts = (
                voucher.base_amount,
                voucher.tax_amount,
                voucher.withheld_amount,
                voucher.exempt_amount,
            )
            if any(voucher.currency_id.compare_amounts(a, 0.0) < 0 for a in amounts):
                raise ValidationError(
                    self.env._(
                        "Los montos de un comprobante de retención de IVA no "
                        "pueden ser negativos."
                    )
                )

    @api.constrains("number")
    def _check_number(self):
        for voucher in self:
            if voucher.number and not VOUCHER_NUMBER_RE.match(voucher.number):
                raise ValidationError(
                    self.env._(
                        "El número de comprobante de retención debe tener 14 "
                        "dígitos: AAAAMM + secuencial de 8 dígitos "
                        "(ej. 20260700000001)."
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        # A voucher is born in draft and its accounting links are set only
        # by the module: over RPC nobody may create it already issued or
        # pointing at an arbitrary journal entry.
        for vals in vals_list:
            if vals.get("state", "draft") != "draft" or (
                L10N_VE_SYSTEM_FIELDS.intersection(vals)
            ):
                raise UserError(
                    self.env._(
                        "Un comprobante de retención se crea en borrador y "
                        "sin vínculos contables: se emite con la acción "
                        "Emitir o al publicar la factura."
                    )
                )
        self._l10n_ve_assign_numbers(vals_list)
        return super().create(vals_list)

    def _l10n_ve_create_unlocked(self, vals_list):
        """Create vouchers from the module's own flows.

        Private on purpose, like ``_l10n_ve_write_unlocked``: not callable
        over RPC.
        """
        self._l10n_ve_assign_numbers(vals_list)
        return super().create(vals_list)

    def _l10n_ve_assign_numbers(self, vals_list):
        for vals in vals_list:
            if not vals.get("number"):
                company = (
                    self.env["res.company"].browse(vals["company_id"])
                    if vals.get("company_id")
                    else self.env.company
                )
                vals["number"] = self._l10n_ve_next_voucher_number(
                    vals.get("date"), company=company
                )

    def write(self, vals):
        # Access errors take precedence over the fiscal lock below.
        self.check_access("write")
        if "state" in vals:
            raise UserError(
                self.env._(
                    "El estado del comprobante de retención solo cambia con "
                    "las acciones Emitir, Anular o Pasar a Borrador."
                )
            )
        if L10N_VE_SYSTEM_FIELDS.intersection(vals):
            raise UserError(
                self.env._(
                    "Los vínculos contables del comprobante de retención "
                    "los gestiona el sistema y no pueden modificarse."
                )
            )
        if L10N_VE_LOCKED_FIELDS.intersection(vals) and any(
            voucher.state != "draft" for voucher in self
        ):
            raise UserError(
                self.env._(
                    "No se pueden modificar los datos fiscales de un "
                    "comprobante de retención emitido o anulado."
                )
            )
        return super().write(vals)

    def _l10n_ve_write_unlocked(self, vals):
        """Write locked fields from the module's own transitions.

        Private on purpose: methods starting with an underscore are not
        callable over RPC, unlike a context key, which the client controls.
        """
        return super().write(vals)

    @api.model
    def _l10n_ve_next_voucher_number(self, seq_date=None, company=None):
        """Return the next monthly, company-specific legal voucher number."""
        company = company or self.env.company
        seq_date = fields.Date.to_date(seq_date) or fields.Date.context_today(self)
        sequence_model = self.env["ir.sequence"].sudo()
        sequence = sequence_model.search(
            [
                ("code", "=", "l10n.ve.iva.wh.voucher"),
                ("company_id", "=", company.id),
            ],
            limit=1,
        )
        if not sequence:
            sequence = sequence_model.create(
                {
                    "name": self.env._(
                        "Comprobante de Retención de IVA (%s)", company.name
                    ),
                    "code": "l10n.ve.iva.wh.voucher",
                    "prefix": "%(year)s%(month)s",
                    "padding": 8,
                    "implementation": "no_gap",
                    "use_date_range": True,
                    "company_id": company.id,
                }
            )
        elif not sequence.use_date_range:
            sequence.use_date_range = True
        date_range_model = self.env["ir.sequence.date_range"].sudo()
        if not date_range_model.search_count(
            [
                ("sequence_id", "=", sequence.id),
                ("date_from", "<=", seq_date),
                ("date_to", ">=", seq_date),
            ]
        ):
            date_range_model.create(
                {
                    "sequence_id": sequence.id,
                    "date_from": seq_date.replace(day=1),
                    "date_to": seq_date.replace(
                        day=calendar.monthrange(seq_date.year, seq_date.month)[1]
                    ),
                }
            )
        number = sequence.next_by_id(sequence_date=seq_date)
        if not number:
            raise UserError(
                self.env._(
                    "No se pudo generar el correlativo de comprobantes de retención "
                    "de IVA (código l10n.ve.iva.wh.voucher) para la compañía %s.",
                    company.display_name,
                )
            )
        return number

    @api.ondelete(at_uninstall=False)
    def _unlink_except_posted(self):
        if any(voucher.state != "draft" for voucher in self):
            raise UserError(
                self.env._(
                    "No se puede eliminar un comprobante de retención emitido "
                    "o anulado: el correlativo fiscal debe conservarse."
                )
            )

    def action_post(self):
        self.check_access("write")
        if any(voucher.state != "draft" for voucher in self):
            raise UserError(
                self.env._("Solo se puede emitir un comprobante en borrador.")
            )
        self._l10n_ve_write_unlocked({"state": "posted"})

    def action_cancel(self):
        self.check_access("write")
        if any(voucher.state == "cancel" for voucher in self):
            raise UserError(self.env._("El comprobante de retención ya está anulado."))
        self._l10n_ve_cancel()

    def _l10n_ve_cancel(self):
        """Cancel the voucher, keeping its number (no-gap sequence).

        Also reached from account.move.button_draft for users that can
        reset their bills but have read-only access to vouchers, hence the
        sudo limited to the voucher itself. The state changes before the
        withholding entries are undone: account.move only protects them
        while their voucher is still posted. The withholding entry and the
        credit note adjustments are cancelled, or reversed when they can no
        longer be modified, so the withheld VAT returns to the vendor
        payable.
        """
        vouchers = self.filtered(lambda voucher: voucher.state != "cancel")
        to_undo = vouchers.filtered(lambda voucher: voucher.state == "posted")
        vouchers.sudo()._l10n_ve_write_unlocked({"state": "cancel"})
        for voucher in to_undo:
            for move in voucher.wh_move_id | voucher.adjustment_ids.move_id:
                self._l10n_ve_undo_move(move)
        self.env["account.move"].search(
            [("l10n_ve_iva_wh_voucher_id", "in", vouchers.ids)]
        ).l10n_ve_iva_wh_voucher_id = False

    def action_draft(self):
        self.check_access("write")
        if any(voucher.state == "cancel" for voucher in self):
            raise UserError(
                self.env._(
                    "Un comprobante anulado no puede volver a borrador: su "
                    "correlativo queda anulado; emita uno nuevo."
                )
            )
        if self.filtered("wh_move_id"):
            raise UserError(
                self.env._(
                    "Un comprobante emitido al contabilizar un documento no puede "
                    "volver a borrador: use «Crear retención de IVA» en el "
                    "documento para emitir uno nuevo."
                )
            )
        self._l10n_ve_write_unlocked({"state": "draft"})

    @api.model
    def _l10n_ve_undo_move(self, move):
        """Cancel, or reverse when it can no longer change, an entry.

        Only called once the entry no longer belongs to a posted voucher,
        which is what account.move checks before letting it be undone.
        """
        if move.state != "posted":
            return
        if move._can_be_unlinked():
            move.button_cancel()
        else:
            move._reverse_moves(
                [
                    {
                        "date": fields.Date.context_today(self),
                        "ref": self.env._("Anulación: %s", move.ref or move.name),
                    }
                ],
                cancel=True,
            )

    @api.model
    def _l10n_ve_wh_period(self, date):
        """Return the first and last day of the withholding period of a date.

        Withholdings are declared and paid by fortnight, days 1 to 15 and
        16 to the end of the month, the same period the TXT export uses.
        """
        date = fields.Date.to_date(date)
        if date.day <= 15:
            return date.replace(day=1), date.replace(day=15)
        last_day = calendar.monthrange(date.year, date.month)[1]
        return date.replace(day=16), date.replace(day=last_day)

    @api.model
    def _l10n_ve_same_wh_period(self, date_a, date_b):
        date_from, date_to = self._l10n_ve_wh_period(date_a)
        date_b = fields.Date.to_date(date_b)
        return bool(date_b) and date_from <= date_b <= date_to

    @api.model
    def _l10n_ve_format_rif(self, vat):
        """Convert J-12345678-9 to the portal format J123456789."""
        return re.sub(r"[^A-Z0-9]", "", (vat or "").upper())

    @api.model
    def _l10n_ve_get_ves_currency(self):
        ves = (
            self.env["res.currency"]
            .with_context(active_test=False)
            .search([("name", "=", "VES")], limit=1)
        )
        if not ves:
            raise UserError(
                self.env._(
                    "No existe la moneda VES (Bolívar) en la base de datos; "
                    "es necesaria para expresar los montos en bolívares."
                )
            )
        return ves

    @api.model
    def _l10n_ve_doc_type(self, move):
        """Return the SENIAT document type."""
        if move.move_type == "in_refund":
            return "03"
        if "debit_origin_id" in move._fields and move.debit_origin_id:
            return "02"
        return "01"

    @api.model
    def _l10n_ve_move_iva_buckets(self, move):
        """Return company-currency VAT bases grouped by legal rate.

        Built from the signed company-currency balances, so a negative line
        (e.g. a discount) lowers its base instead of adding to it. Amounts
        are positive for both bills and credit notes.
        """
        sign = move.direction_sign
        buckets = {}
        exempt = 0.0
        for line in move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        ):
            amount = sign * line.balance
            leaf_taxes = line.tax_ids
            group_taxes = leaf_taxes.filtered(lambda tax: tax.amount_type == "group")
            leaf_taxes = (leaf_taxes - group_taxes) | group_taxes.children_tax_ids
            rate = round(sum(leaf_taxes.mapped("amount")), 2)
            if rate:
                bucket = buckets.setdefault(rate, {"base": 0.0, "tax": 0.0})
                bucket["base"] += amount
            else:
                exempt += amount
        for tax_line in move.line_ids.filtered("tax_line_id"):
            rate = round(tax_line.tax_line_id.amount, 2)
            if not rate:
                continue
            bucket = buckets.setdefault(rate, {"base": 0.0, "tax": 0.0})
            bucket["tax"] += sign * tax_line.balance
        return buckets, exempt

    @api.model
    def _l10n_ve_move_iva_amounts(self, move):
        """Return aggregate VAT amounts for a document in company currency."""
        buckets, exempt = self._l10n_ve_move_iva_buckets(move)
        base = sum(bucket["base"] for bucket in buckets.values())
        tax = sum(bucket["tax"] for bucket in buckets.values())
        rate = round(tax / base * 100.0, 2) if base else 0.0
        return {"base": base, "exempt": exempt, "tax": tax, "rate": rate}

    @api.model
    def _l10n_ve_move_withheld(self, move, rate):
        """Return the VAT withheld on a document, in company currency.

        Computed on the company-currency (bolivar) VAT of the document and
        rounded there, so the voucher ties to the document's figures; the
        document currency amount is derived from it, never the reverse.
        """
        tax = self._l10n_ve_move_iva_amounts(move)["tax"]
        return move.company_id.currency_id.round(tax * rate / 100.0)

    @api.model
    def _l10n_ve_move_sign(self, move):
        """Return -1 for credit notes, which reduce the voucher totals."""
        return -1.0 if move.move_type in ("in_refund", "out_refund") else 1.0

    def _l10n_ve_compute_totals(self):
        """Return the net totals of an issued voucher from its documents."""
        self.ensure_one()
        totals = dict.fromkeys(
            ("base_amount", "tax_amount", "exempt_amount", "withheld_amount"), 0.0
        )
        for move in self.move_ids:
            sign = self._l10n_ve_move_sign(move)
            amounts = self._l10n_ve_move_iva_amounts(move)
            totals["base_amount"] += sign * amounts["base"]
            totals["tax_amount"] += sign * amounts["tax"]
            totals["exempt_amount"] += sign * amounts["exempt"]
            totals["withheld_amount"] += sign * self._l10n_ve_move_withheld(
                move, self.wh_rate
            )
        return totals

    def _l10n_ve_get_amount_for_move(self, move):
        """Return the (positive) withholding attributable to a document."""
        self.ensure_one()
        if not move or move not in self.move_ids:
            return 0.0
        if self.wh_move_id:
            return self._l10n_ve_move_withheld(move, self.wh_rate)
        # Manually entered voucher: spread its amount by VAT.
        taxes = {
            current_move.id: self._l10n_ve_move_iva_amounts(current_move)["tax"]
            for current_move in self.move_ids
        }
        total_tax = sum(taxes.values())
        if not self.currency_id.is_zero(total_tax):
            share = taxes[move.id] / total_tax
        else:
            share = 1.0 / len(self.move_ids)
        return self.currency_id.round(self.withheld_amount * share)

    def _l10n_ve_get_report_lines(self):
        """Return one report and TXT line per document and legal VAT rate.

        Amounts are always positive: a credit note is told apart by its
        document type (03) and the affected invoice, as Form 99035 expects.
        """
        self.ensure_one()
        lines = []
        for move in self.move_ids:
            buckets, exempt = self._l10n_ve_move_iva_buckets(move)
            move_withheld = self._l10n_ve_get_amount_for_move(move)
            move_tax = sum(bucket["tax"] for bucket in buckets.values())
            control_number = (
                move.l10n_ve_control_number
                if "l10n_ve_control_number" in move._fields
                else False
            )
            common = {
                "move": move,
                "doc_type": self._l10n_ve_doc_type(move),
                "doc_number": move.ref or move.name or "",
                "control_number": control_number or "",
                "affected": (
                    move.reversed_entry_id.ref or move.reversed_entry_id.name or ""
                ),
                "date": move.invoice_date or move.date,
                "total": abs(move.amount_total_signed),
            }
            if not buckets:
                lines.append(
                    {
                        **common,
                        "base": 0.0,
                        "exempt": exempt,
                        "tax": 0.0,
                        "rate": 0.0,
                        "withheld": 0.0,
                    }
                )
                continue
            sorted_rates = sorted(buckets)
            remaining = move_withheld
            for index, rate in enumerate(sorted_rates):
                bucket = buckets[rate]
                if index == len(sorted_rates) - 1:
                    withheld = remaining
                else:
                    withheld = self.currency_id.round(
                        move_withheld * (bucket["tax"] / move_tax) if move_tax else 0.0
                    )
                    remaining -= withheld
                lines.append(
                    {
                        **common,
                        "base": bucket["base"],
                        "exempt": exempt if index == 0 else 0.0,
                        "tax": bucket["tax"],
                        "rate": rate,
                        "withheld": withheld,
                    }
                )
        return lines
