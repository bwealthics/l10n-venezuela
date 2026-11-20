# Copyright 2026 BWEALTHICS LLC
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from datetime import date

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from .common import L10nVeSetupMixin


@tagged("post_install", "-at_install")
class TestEmissionMedium(L10nVeSetupMixin, AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._l10n_ve_setup()
        cls.customer = cls._create_ve_partner("Cliente Fiscal", "V123456781")
        cls.sale_journal = cls.company_data["default_journal_sale"]

    def _create_invoice(self, medium, nro_ctrl=False):
        self.sale_journal.l10n_ve_emission_medium = medium
        return self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "journal_id": self.sale_journal.id,
                "invoice_date": "2025-09-01",
                "invoice_date_due": "2025-09-15",
                "nro_ctrl": nro_ctrl,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Servicio",
                            "quantity": 1,
                            "price_unit": 100.0,
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
        )

    def _post_digital(self):
        move = self._create_invoice("digital")
        move.action_post()
        return move

    def test_medium_kept_on_posted_invoice(self):
        move = self._create_invoice("digital")
        self.assertEqual(move.l10n_ve_emission_medium, "digital")
        self.sale_journal.l10n_ve_emission_medium = "fiscal_machine"
        move.action_post()
        self.assertEqual(move.l10n_ve_emission_medium, "fiscal_machine")
        self.sale_journal.l10n_ve_emission_medium = "free"
        self.assertEqual(move.l10n_ve_emission_medium, "fiscal_machine")
        with self.assertRaises(UserError):
            move.write({"l10n_ve_emission_medium": "free"})

    def test_preprinted_medium_requires_control_number(self):
        for medium, number in (
            ("free", "00-00000101"),
            ("contingency", "00-00000102"),
        ):
            with self.subTest(medium=medium):
                move = self._create_invoice(medium)
                with self.assertRaises(UserError):
                    move.action_post()
                move.nro_ctrl = number
                move.action_post()
                self.assertEqual(move.state, "posted")

    def test_post_assigned_medium_posts_without_control_number(self):
        for medium in ("digital", "fiscal_machine"):
            with self.subTest(medium=medium):
                move = self._create_invoice(medium)
                move.action_post()
                self.assertEqual(move.state, "posted")
                self.assertFalse(move.nro_ctrl)

    def test_assign_control_data_once(self):
        move = self._post_digital()
        move._l10n_ve_assign_control_data(" 00-00000201 ", date(2025, 9, 2))
        self.assertEqual(move.nro_ctrl, "00-00000201")
        self.assertEqual(move.l10n_ve_control_date, date(2025, 9, 2))
        self.assertTrue(
            move._l10n_ve_assign_control_data("00-00000201", date(2025, 9, 5))
        )
        self.assertEqual(move.l10n_ve_control_date, date(2025, 9, 2))
        with self.assertRaises(UserError):
            move._l10n_ve_assign_control_data("00-00000202")
        with self.assertRaises(UserError):
            move.write({"l10n_ve_control_date": date(2025, 9, 3)})
        self.assertEqual(move.nro_ctrl, "00-00000201")

    def test_assign_control_data_rejects_duplicate(self):
        first = self._post_digital()
        second = self._post_digital()
        first._l10n_ve_assign_control_data("00-00000301")
        self.assertTrue(first.l10n_ve_control_date)
        with self.assertRaises(ValidationError):
            second._l10n_ve_assign_control_data("00-00000301")

    def test_assign_control_data_rejected_on_wrong_document(self):
        draft = self._create_invoice("digital")
        free_form = self._create_invoice("free", nro_ctrl="00-00000401")
        free_form.action_post()
        for move in (draft, free_form):
            with self.subTest(move=move.display_name):
                with self.assertRaises(UserError):
                    move._l10n_ve_assign_control_data("00-00000402")
        with self.assertRaises(UserError):
            self._post_digital()._l10n_ve_assign_control_data(" ")

    def test_non_ve_company_unaffected(self):
        self.company.account_fiscal_country_id = self.env.ref("base.us")
        move = self._create_invoice("free")
        self.assertFalse(move.l10n_ve_emission_medium)
        move.action_post()
        self.assertEqual(move.state, "posted")
        self.assertFalse(move.nro_ctrl)
        with self.assertRaises(UserError):
            move._l10n_ve_assign_control_data("00-00000501")
