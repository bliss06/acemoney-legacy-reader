"""Smoke tests for user-supplied sample.mmw; run: python3 -m unittest test_acemoney_reader.py"""
import unittest
from pathlib import Path
from decimal import Decimal
from acemoney_reader import load_mmw, monthly_rows, transaction_rows

SAMPLE = Path(__file__).with_name('sample.mmw')

class SampleMMWTests(unittest.TestCase):
    def setUp(self):
        self.book = load_mmw(SAMPLE)

    def test_reference_counts(self):
        self.assertEqual(len(self.book.accounts), 1)
        self.assertEqual(len(self.book.banks), 207)
        self.assertEqual(len(self.book.categories), 120)
        self.assertEqual(len(self.book.payees), 8)
        self.assertEqual(len(self.book.schedules), 4)
        self.assertEqual(len(self.book.transactions), 5)

    def test_known_transactions(self):
        recs = list(transaction_rows(self.book))
        self.assertEqual(recs[0]['Date'], '2004-12-01')
        self.assertEqual(recs[0]['Payee'], 'Employer')
        self.assertEqual(recs[0]['Category'], 'Wages and Salary:Net Pay')
        self.assertEqual(recs[0]['Amount'], '1500.00')
        self.assertEqual(recs[2]['Note'], 'Home phone bill')
        self.assertEqual(recs[3]['Amount'], '-50.00')
        self.assertEqual(recs[-1]['Amount'], '-255.00')

    def test_monthly_balance(self):
        row, = monthly_rows(self.book)
        self.assertEqual(row['Month'], '2004-12')
        self.assertEqual(Decimal(row['Income']), Decimal('1500.00'))
        self.assertEqual(Decimal(row['Expenses']), Decimal('1135.00'))
        self.assertEqual(Decimal(row['Net']), Decimal('365.00'))
        self.assertEqual(self.book.account_types[2], 'Bank accounts')

@unittest.skipUnless(Path(__file__).with_name('IVF.mmw').exists(),
                     'Optional private IVF.mmw test file not included in package')
class HistoricalMMWTests(unittest.TestCase):
    """Verify a file with a nonempty account description and trailing metadata."""
    def setUp(self):
        self.book = load_mmw(Path(__file__).with_name('IVF.mmw'))

    def test_reference_counts_and_comment(self):
        b = self.book
        self.assertEqual((len(b.accounts), len(b.banks), len(b.categories),
                          len(b.payees), len(b.transactions), len(b.schedules)),
                         (1, 2, 16, 0, 30, 0))
        self.assertEqual(b.accounts[0].name, 'IVF 2010')
        self.assertTrue(b.accounts[0].comment)
        self.assertEqual(b.account_types[6], 'Expenses')

    def test_transactions_and_dates(self):
        recs = list(transaction_rows(self.book))
        self.assertEqual(len(recs), 30)
        self.assertEqual(recs[0]['Date'], '2010-10-07')
        self.assertEqual(recs[-1]['Date'], '2010-10-15')
        self.assertEqual(recs[0]['Amount'], '-1032.00')
        self.assertEqual(recs[-1]['Amount'], '-900.00')
        self.assertTrue(all(r['Account ID'] == 3 for r in recs))

    def test_october_total(self):
        row, = list(monthly_rows(self.book))
        self.assertEqual(row['Month'], '2010-10')
        self.assertEqual(Decimal(row['Expenses']), Decimal('51536.00'))
        self.assertEqual(row['Transactions'], 30)


# v1.2 regression tests: category descriptions are length-prefixed strings, not flags.
# These tests use the public bundled sample, with synthetic extra bytes for the
# lengthy description case; no private financial file is required.
class CategoryDescriptionTests(unittest.TestCase):
    def test_nonempty_category_description(self):
        import tempfile
        from acemoney_reader import Reader
        data = SAMPLE.read_bytes()
        r = Reader(data)
        for _ in range(r.count('accounts')):
            r.u32(); r.s(); r.f64(); r.u32(); r.u32(); r.u8(); r.s(); r.u32()
        for _ in range(r.count('banks')):
            r.u32(); r.s(); r.s(); r.s(); r.s()
        count = r.count('categories')
        self.assertEqual(count, 120)
        r.u32()
        r.s()
        description_position = r.pos
        self.assertEqual(data[description_position], 0)
        description = b'Long category details with commas, colons: and extra strings.'
        revised = (data[:description_position] + bytes([len(description)]) +
                   description + data[description_position+1:])
        with tempfile.TemporaryDirectory() as folder:
            f = Path(folder) / 'synthetic_descriptions.mmw'
            f.write_bytes(revised)
            book = load_mmw(f)
        self.assertEqual(book.category_comments[1], description.decode())
        self.assertEqual(len(book.transactions), 5)
        self.assertEqual(len(book.categories), 120)

    def test_invalid_category_is_rejected_not_silently_misparsed(self):
        import tempfile
        from acemoney_reader import Reader, FormatError
        data = SAMPLE.read_bytes()
        r = Reader(data)
        for _ in range(r.count('accounts')):
            r.u32(); r.s(); r.f64(); r.u32(); r.u32(); r.u8(); r.s(); r.u32()
        for _ in range(r.count('banks')):
            r.u32(); r.s(); r.s(); r.s(); r.s()
        r.count('categories')
        first_category_offset = r.pos
        modified = bytearray(data)
        modified[first_category_offset:first_category_offset+4] = (0).to_bytes(4,'little')
        with tempfile.TemporaryDirectory() as folder:
            f = Path(folder) / 'bad_category.mmw'
            f.write_bytes(modified)
            with self.assertRaises(FormatError):
                load_mmw(f)

@unittest.skipUnless((Path(__file__).with_name('Model Jan-Dec09.mmw').exists() and
                      Path(__file__).with_name('Apr09(1).mmw').exists()),
                     'Optional private 2009 files not included in package')
class Monthly2009Tests(unittest.TestCase):
    def test_model_is_category_template(self):
        b = load_mmw(Path(__file__).with_name('Model Jan-Dec09.mmw'))
        self.assertEqual(len(b.accounts), 1)
        self.assertEqual(len(b.categories), 201)
        self.assertEqual(len(b.transactions), 0)
        self.assertEqual(b.account_types[6], 'Expenses')
        self.assertTrue(b.category_comments[191].startswith('Toor, Urad'))

    def test_april_recovers_all_transaction_data(self):
        b = load_mmw(Path(__file__).with_name('Apr09(1).mmw'))
        self.assertEqual(len(b.accounts), 1)
        self.assertEqual(len(b.categories), 198)
        self.assertEqual(len(b.transactions), 120)
        month_rows = list(monthly_rows(b,5.5))
        self.assertEqual(len(month_rows), 1)
        self.assertEqual(month_rows[0]['Month'], '2009-04')
        self.assertEqual(month_rows[0]['Transactions'], 120)
        self.assertEqual(b.category_comments[191], load_mmw(Path(__file__).with_name('Model Jan-Dec09.mmw')).category_comments[191])

if __name__ == '__main__':
    unittest.main()
