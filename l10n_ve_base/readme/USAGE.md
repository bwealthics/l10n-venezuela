When posting customer invoices or vendor bills:

1.  Sales: Odoo never generates the control number. With a free form
    or contingency journal, type it from the pre-printed form before
    posting. With a digital printing house or fiscal machine journal,
    the invoice posts without it and the connector writes the number
    and its date back once, through
    `account.move._l10n_ve_assign_control_data(control_number,
    control_date=None)`. An existing number is never replaced;
    repeating the same number is a no-op, so connectors can retry.
2.  Purchases: enter the pre-printed SENIAT control number.
3.  The partner RIF must be valid when the partner is Venezuelan.
4.  Invoice total cannot be zero; due date cannot be before the invoice
    date; credit notes need a source invoice; one aliquot per line.
5.  Contacts can be checked via Accounting \> Customers \> Consult
    Partner in SENIAT.
6.  Printed invoices show RIF, control number, municipality and parish
    when the company is Venezuelan.
