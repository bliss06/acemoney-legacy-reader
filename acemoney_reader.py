#!/usr/bin/env python3
"""AceMoney Classic legacy .MMW read-only viewer and CSV exporter, v1.3.

Reverse-engineered from AceMoney v3.x sample.mmw and 2009/2010 user files. No AceMoney executables
are launched and NO .MMW files are modified.

Tested with a bundled demo file and multiple historical monthly files. Other versions, split transactions,
complex investments, protected/corrupt files may require format extensions.

Usage:
    python3 acemoney_reader.py                 # choose a file via GUI
    python3 acemoney_reader.py old_book.mmw    # load a specific file
    python3 acemoney_reader.py --inspect old_book.mmw
    python3 acemoney_reader.py --export-csv old_book.mmw output.csv

Requires Python 3 with Tkinter for GUI; CLI modes need only stdlib.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import struct
import tempfile
import argparse
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

MAX_ROWS = 2_000_000
MAX_STRING = 10_000_000

class FormatError(ValueError):
    pass

class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise FormatError(f"Unexpected end of file at byte {self.pos:,}, need {n:,} bytes")
        v = self.data[self.pos:self.pos+n]
        self.pos += n
        return v

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack('<H', self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack('<I', self.take(4))[0]

    def f64(self) -> float:
        return struct.unpack('<d', self.take(8))[0]

    def s(self) -> str:
        # Legacy CString encoding: 1-byte length, FF + uint16 for longer text,
        # and FFFF + uint32 for very long text.
        size = self.u8()
        if size == 255:
            size = self.u16()
            if size == 65535:
                size = self.u32()
        if size > MAX_STRING:
            raise FormatError(f"Unexpected string length {size:,} at byte {self.pos:,}")
        raw = self.take(size)
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            return raw.decode('cp1252', 'replace')

    def count(self, label: str) -> int:
        n = self.u32()
        if n > MAX_ROWS or n * 5 > len(self.data) - self.pos + 4:
            raise FormatError(f"Implausible {label} count {n:,} near byte {self.pos-4:,}; "
                              "this may be a different MMW version or a protected file")
        return n

@dataclass
class Account:
    id: int
    name: str
    opening_balance: float
    account_type_id: int
    currency_id: int
    bank_id: int
    flags: int
    comment: str = ''

@dataclass
class Transaction:
    id: int
    status_code: int
    timestamp: int
    payee_id: int
    category_id: int
    account_id: int
    linked_account_id: int
    amount: float
    split_code: int
    transaction_type_code: int
    fields: list[str]

@dataclass
class Schedule:
    id: int
    first_timestamp: int
    frequency_code: int
    frequency_detail: int
    status_code: int
    next_timestamp: int
    payee_id: int
    category_id: int
    account_id: int
    linked_account_id: int
    amount: float
    split_code: int
    transaction_type_code: int
    fields: list[str]

@dataclass
class MMWBook:
    source: str
    bytes_read: int
    total_size: int
    accounts: list[Account]
    banks: dict[int, str]
    categories: dict[int, str]
    category_comments: dict[int, str]
    payees: dict[int, str]
    account_types: dict[int, str]
    transactions: list[Transaction]
    schedules: list[Schedule]
    warnings: list[str] = field(default_factory=list)

    def account_label(self, ident: int) -> str:
        return next((a.name for a in self.accounts if a.id == ident), f'Account #{ident}')

    def category_label(self, ident: int) -> str:
        return self.categories.get(ident, '' if ident == 0 else f'Category #{ident}')

    def payee_label(self, ident: int) -> str:
        return self.payees.get(ident, '' if ident == 0 else f'Payee #{ident}')


def load_mmw(path: str | Path) -> MMWBook:
    filename = Path(path)
    data = filename.read_bytes()
    r = Reader(data)

    # Legacy order in all four inspected .mmw files:
    # accounts; banks; categories; payees; transactions; schedules;
    # account type dictionary; miscellaneous settings/other data.
    account_count = r.count('accounts')
    accounts = []
    for _ in range(account_count):
        # Account record: ID, name, initial balance, type, currency, 1-byte
        # flags, optional comment, then bank ID. In the bundled demonstration
        # file the comment is empty, hiding this field in the original MVP.
        account_id, account_name, balance = r.u32(), r.s(), r.f64()
        type_id, currency_id = r.u32(), r.u32()
        flags, comment, bank_id = r.u8(), r.s(), r.u32()
        accounts.append(Account(account_id, account_name, balance,
                                type_id, currency_id, bank_id, flags, comment))

    banks = {}
    for _ in range(r.count('banks')):
        ident, name = r.u32(), r.s()
        # Address, website, comment are present but not shown in this MVP.
        _address, _website, _comment = r.s(), r.s(), r.s()
        banks[ident] = name

    # Category format is (id:u32, name:CString, description:CString).
    # Many categories have an empty description (one 00 byte); early
    # versions mistakenly treated that byte as a flag. Real files contain
    # lengthy category descriptions, so reading just one byte loses alignment.
    categories, category_comments = {}, {}
    for _ in range(r.count('categories')):
        ident, name, description = r.u32(), r.s(), r.s()
        if ident == 0 or ident in categories or not name.strip():
            raise FormatError(
                f'Invalid or duplicate category ID/name near byte {r.pos:,}; '
                'the file may use a different category layout')
        categories[ident] = name
        category_comments[ident] = description

    payees = {}
    for _ in range(r.count('payees')):
        ident, name = r.u32(), r.s()
        _address, _comment = r.s(), r.s()
        payees[ident] = name

    transactions = []
    for _ in range(r.count('transactions')):
        t = Transaction(
            id=r.u32(), status_code=r.u8(), timestamp=r.u32(),
            payee_id=r.u32(), category_id=r.u32(), account_id=r.u32(),
            linked_account_id=r.u32(), amount=r.f64(), split_code=r.u32(),
            transaction_type_code=r.u32(), fields=[r.s() for _ in range(5)]
        )
        transactions.append(t)

    schedules = []
    for _ in range(r.count('schedules')):
        schedules.append(Schedule(
            id=r.u32(), first_timestamp=r.u32(), frequency_code=r.u32(),
            frequency_detail=r.u32(), status_code=r.u8(), next_timestamp=r.u32(),
            payee_id=r.u32(), category_id=r.u32(), account_id=r.u32(),
            linked_account_id=r.u32(), amount=r.f64(), split_code=r.u32(),
            transaction_type_code=r.u32(), fields=[r.s() for _ in range(5)]
        ))

    # After schedules, the sample has an empty CString; the 2010 file has
    # a nonempty CString. Its value may contain private metadata, so we skip
    # it without decoding/printing, followed by the account-type dictionary.
    # Other files may omit this CString: try both forms safely.
    acc_types = {}
    if r.pos < len(data):
        saved = r.pos
        for with_metadata in (True, False):
            r.pos = saved
            try:
                if with_metadata:
                    n = r.u8()
                    if n == 255:
                        n = r.u16()
                        if n == 65535:
                            n = r.u32()
                    if n > 2048:
                        raise FormatError('Metadata too large')
                    r.take(n)
                type_count = r.count('account types')
                if not 1 <= type_count <= 50:
                    raise FormatError('Implausible account-type count')
                candidate_types = {}
                for _ in range(type_count):
                    ident, name = r.u32(), r.s()
                    if ident > 100 or not (1 <= len(name) <= 80):
                        raise FormatError('Unexpected account-type entry')
                    candidate_types[ident] = name
                if len(candidate_types) != type_count:
                    raise FormatError('Duplicate account-type ID')
                acc_types = candidate_types
                break
            except FormatError:
                r.pos = saved

    book = MMWBook(str(filename), r.pos, len(data), accounts, banks, categories,
                   category_comments, payees, acc_types, transactions, schedules)
    if any(x.split_code != 0 for x in transactions):
        book.warnings.append('Some transactions have nonzero split/reference fields. '
                             'Split line items are not decoded separately: totals and '
                             'category assignments should be checked against AceMoney.')
    unknown_accounts = {t.account_id for t in transactions if t.account_id not in {a.id for a in accounts}}
    if unknown_accounts:
        raise FormatError(f'Transactions reference missing account IDs: {sorted(unknown_accounts)}')
    missing_categories = {t.category_id for t in transactions if t.category_id and t.category_id not in categories}
    if missing_categories:
        book.warnings.append(f'{len(missing_categories)} category IDs in transactions were not found in category dictionary.')
    missing_payees = {t.payee_id for t in transactions if t.payee_id and t.payee_id not in payees}
    if missing_payees:
        book.warnings.append(f'{len(missing_payees)} payee IDs in transactions were not found in payee dictionary.')
    if any(not (-1e13 < t.amount < 1e13) for t in transactions):
        raise FormatError('Implausible transaction amount detected; record alignment may be wrong')
    if len(data) > book.bytes_read:
        book.warnings.append(f'{len(data)-book.bytes_read:,} trailing bytes (investments, '
                             'currency settings, saved reports, etc.) were not decoded.')
    return book


def local_timezone_shift_hours() -> float:
    """Best-effort current machine UTC offset for the initial UI setting.

    The user can override it for dates created under a different timezone.
    """
    offset = datetime.now().astimezone().utcoffset()
    return (offset.total_seconds() / 3600) if offset else 0.0


def formatted_date(seconds: int, hour_shift: float = 0) -> str:
    if seconds == 0:
        return ''
    try:
        return (datetime.fromtimestamp(seconds, timezone.utc)
                + timedelta(hours=hour_shift)).strftime('%Y-%m-%d')
    except (OSError, ValueError, OverflowError):
        return f'UNREADABLE:{seconds}'


def money(d: float) -> str:
    return f'{d:,.2f}'


def transaction_rows(book: MMWBook, hour_shift: float = 0):
    for t in book.transactions:
        yield {
            'Date': formatted_date(t.timestamp, hour_shift),
            'Account': book.account_label(t.account_id),
            'Payee': book.payee_label(t.payee_id),
            'Category': book.category_label(t.category_id),
            'Amount': f'{t.amount:.2f}',
            'Note': t.fields[0],
            'Additional Fields': ' | '.join(v for v in t.fields[1:] if v),
            'Transaction ID': t.id,
            'Account ID': t.account_id,
            'Category ID': t.category_id,
            'Payee ID': t.payee_id,
            'Status Code': t.status_code,
            'Type Code': t.transaction_type_code,
            'Linked Account ID': t.linked_account_id,
            'Split Code': t.split_code,
            'Raw Timestamp': t.timestamp,
        }


# Column order for transaction exports, including files with no transactions.
TRANSACTION_CSV_FIELDS = [
    'Date', 'Account', 'Payee', 'Category', 'Amount', 'Note',
    'Additional Fields', 'Transaction ID', 'Account ID', 'Category ID',
    'Payee ID', 'Status Code', 'Type Code', 'Linked Account ID',
    'Split Code', 'Raw Timestamp',
]


def bulk_export_transactions(files: list[str | Path], destination: str | Path,
                             hour_shift: float = 0, overwrite: bool = False):
    """Export each MMW's transactions to its own same-stem CSV.

    Returns (successes, skips, failures), where successes contain
    (input_name, transaction_count) and failures contain (input_name, message).
    Originals are never modified. Existing CSVs are skipped unless overwrite=True.
    Files with zero transactions receive a header-only CSV.
    """
    folder = Path(destination)
    if not folder.is_dir():
        raise ValueError(f'Output folder does not exist: {folder}')

    successes, skips, failures = [], [], []
    seen_names = set()
    for source in files:
        source = Path(source)
        target = folder / (source.stem + '.csv')
        collision_key = target.name.casefold()
        if collision_key in seen_names:
            skips.append((source.name, 'same CSV name as another selected file'))
            continue
        seen_names.add(collision_key)
        if target.exists() and not overwrite:
            skips.append((source.name, f'CSV already exists: {target.name}'))
            continue
        try:
            book = load_mmw(source)
            rows = list(transaction_rows(book, hour_shift))
            # Write to a temporary CSV first; a bad MMW must not leave a
            # truncated CSV in the chosen destination folder.
            fd, temporary = tempfile.mkstemp(prefix='.acemoney-export-',
                                              suffix='.csv', dir=folder)
            os.close(fd)
            try:
                if rows:
                    csv_export(temporary, rows, source)
                else:
                    with open(temporary, 'w', newline='', encoding='utf-8-sig') as out:
                        csv.DictWriter(out, fieldnames=TRANSACTION_CSV_FIELDS).writeheader()
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            successes.append((source.name, len(rows)))
        except (OSError, ValueError, struct.error, UnicodeError) as exc:
            failures.append((source.name, str(exc)))
    return successes, skips, failures


def monthly_rows(book: MMWBook, hour_shift: float = 0):
    stats = defaultdict(lambda: {'income': Decimal('0'), 'expense': Decimal('0'), 'count': 0})
    for t in book.transactions:
        month = formatted_date(t.timestamp, hour_shift)[:7]
        key = (t.account_id, month)
        val = Decimal(str(t.amount))
        if val >= 0:
            stats[key]['income'] += val
        else:
            stats[key]['expense'] += -val
        stats[key]['count'] += 1
    for (account_id, month), row in sorted(stats.items(), key=lambda x: (x[0][0], x[0][1])):
        yield {'Month': month, 'Account': book.account_label(account_id),
               'Income': f"{row['income']:.2f}", 'Expenses': f"{row['expense']:.2f}",
               'Net': f"{row['income'] - row['expense']:.2f}",
               'Transactions': row['count']}


def csv_export(path: str | Path, rows: list[dict], protected_input: str | Path | None = None):
    target = Path(path)
    if target.suffix.lower() == '.mmw' or (protected_input is not None and
        target.resolve() == Path(protected_input).resolve()):
        raise ValueError('Refusing to overwrite an AceMoney source file. Choose a new .csv filename.')
    if not rows:
        raise ValueError('No rows to export')
    with target.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def make_gui(initial_path: str | None = None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    class Viewer:
        def __init__(self, root):
            self.root = root
            self.root.title('AceMoney Legacy Reader — read only')
            self.root.geometry('1190x750')
            self.book = None
            self.tx_cache = []
            self.month_cache = []
            self.account_choices = {}
            self.shift = tk.StringVar(value=f'{local_timezone_shift_hours():g}')
            self.search = tk.StringVar()
            self.selected_account = tk.StringVar(value='All accounts')
            self.status = tk.StringVar(value='Choose File → Open .MMW to begin. Your files will never be modified.')

            toolbar = ttk.Frame(root, padding=(10, 10))
            toolbar.pack(fill='x')
            ttk.Button(toolbar, text='Open .MMW…', command=self.open_dialog).pack(side='left')
            ttk.Button(toolbar, text='Export transactions CSV…', command=self.export_transactions).pack(side='left', padx=(8, 0))
            ttk.Button(toolbar, text='Export monthly CSV…', command=self.export_monthly).pack(side='left', padx=(8, 0))
            ttk.Button(toolbar, text='Bulk Export CSV…', command=self.bulk_export).pack(side='left', padx=(8, 0))
            ttk.Label(toolbar, text='Read-only · offline', foreground='#457a46').pack(side='right')

            filters = ttk.Frame(root, padding=(10, 4))
            filters.pack(fill='x')
            ttk.Label(filters, text='Account:').pack(side='left')
            self.accounts_combo = ttk.Combobox(filters, textvariable=self.selected_account, state='readonly', width=35)
            self.accounts_combo.pack(side='left', padx=(4, 16))
            self.accounts_combo.bind('<<ComboboxSelected>>', self.refresh)
            ttk.Label(filters, text='Search:').pack(side='left')
            search = ttk.Entry(filters, textvariable=self.search, width=27)
            search.pack(side='left', padx=(4, 16))
            self.search.trace_add('write', lambda *_: self.refresh())
            ttk.Label(filters, text='Date shift (hours):').pack(side='left')
            shifts = ['0','-12','-8','-5','-4','-1','1','2','3','4','5','5.5','6','8','9','10','12','13']
            self.shift_combo = ttk.Combobox(filters, textvariable=self.shift, values=shifts, width=7)
            self.shift_combo.pack(side='left', padx=4)
            self.shift_combo.bind('<<ComboboxSelected>>', self.on_shift)
            self.shift_combo.bind('<Return>', self.on_shift)

            self.tabs = ttk.Notebook(root)
            self.tabs.pack(fill='both', expand=True, padx=10, pady=7)
            self.views = {}
            self.create_tab('Transactions', ['Date','Account','Payee','Category','Amount','Note','Additional Fields','Transaction ID'])
            self.create_tab('Monthly summary', ['Month','Account','Income','Expenses','Net','Transactions'])
            self.create_tab('Scheduled', ['Next date','Account','Payee','Category','Amount','Note','Frequency code','ID'])
            self.create_tab('Accounts', ['ID','Account','Type','Description','Bank','Currency ID','Opening balance','Transactions','Calculated balance'])
            self.create_tab('Categories', ['ID','Category','Description'])
            self.create_tab('Payees', ['ID','Payee'])

            self.details = tk.Text(self.tabs, wrap='word', height=12, borderwidth=0, padx=14, pady=12)
            self.details.configure(state='disabled')
            self.tabs.add(self.details, text='File details')
            ttk.Label(root, textvariable=self.status, anchor='w', padding=(12, 5)).pack(fill='x')
            if initial_path:
                self.open_file(initial_path)

        def create_tab(self, name, cols):
            frame = ttk.Frame(self.tabs)
            self.tabs.add(frame, text=name)
            tree = ttk.Treeview(frame, columns=cols, show='headings', selectmode='browse')
            vsb = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
            hsb = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
            tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            tree.grid(row=0, column=0, sticky='nsew')
            vsb.grid(row=0, column=1, sticky='ns')
            hsb.grid(row=1, column=0, sticky='ew')
            for col in cols:
                w = 110
                if col in ('Account','Payee','Category','Note','Description'):
                    w = 180
                if col == 'Additional Fields':
                    w = 230
                if col in ('ID','Transaction ID','Frequency code'):
                    w = 85
                tree.heading(col, text=col)
                tree.column(col, width=w, minwidth=65, anchor='e' if col in
                            ('Amount','Opening balance','Calculated balance','Income','Expenses','Net') else 'w')
            self.views[name] = (tree, cols)

        def fill(self, tab, rows):
            tree, cols = self.views[tab]
            tree.delete(*tree.get_children())
            for i, rec in enumerate(rows):
                vals = tuple(rec.get(k, '') for k in cols)
                tree.insert('', 'end', values=vals, tags=('stripe' if i % 2 else '',))
            tree.tag_configure('stripe', background='#f4f7f9')

        def open_dialog(self):
            name = filedialog.askopenfilename(title='Open original AceMoney file',
                       filetypes=[('AceMoney files','*.mmw'),('All files','*.*')])
            if name:
                self.open_file(name)

        def open_file(self, path):
            try:
                b = load_mmw(path)
            except (ValueError, OSError, struct.error, UnicodeError) as err:
                messagebox.showerror('Cannot decode this MMW file',
                                     f'{path}\n\n{err}\n\n'
                                     'Your original file has not been changed. '
                                     'This reader supports the studied legacy layouts; '
                                     'other versions may need adjustments.')
                return
            self.book = b
            self.search.set('')
            self.account_choices = {'All accounts': None}
            for a in b.accounts:
                self.account_choices[f'{a.name} (#{a.id})'] = a.id
            self.accounts_combo['values'] = list(self.account_choices)
            self.selected_account.set('All accounts')
            self.root.title(f'AceMoney Legacy Reader — {Path(path).name} — read only')
            self.render_static()
            self.on_shift()
            message = (f'{Path(path).name}: {len(b.accounts)} accounts · '
                       f'{len(b.transactions)} transactions · '
                       f'{len(b.schedules)} scheduled payments')
            if b.warnings:
                message += ' · See File details for limitations'
            self.status.set(message)

        def date_shift(self):
            try:
                val = float(self.shift.get())
                if not -24 <= val <= 24:
                    raise ValueError('offset too large')
                return val
            except ValueError:
                messagebox.showwarning('Date adjustment','Enter an hour offset from -24 to +24.')
                self.shift.set('0')
                return 0

        def on_shift(self, *_):
            if self.book is None:
                return
            shift = self.date_shift()
            self.tx_cache = list(transaction_rows(self.book, shift))
            self.month_cache = list(monthly_rows(self.book, shift))
            self.render_scheduled(shift)
            self.refresh()

        def matches(self, row):
            target_id = self.account_choices.get(self.selected_account.get())
            if target_id is not None:
                if row.get('Account') != self.book.account_label(target_id):
                    return False
            q = self.search.get().strip().casefold()
            return not q or any(q in str(v).casefold() for v in row.values())

        def refresh(self, *_):
            if self.book is None:
                return
            transactions = [x for x in self.tx_cache if self.matches(x)]
            self.fill('Transactions', transactions)
            selected = self.account_choices.get(self.selected_account.get())
            months = [x for x in self.month_cache if
                      selected is None or x['Account'] == self.book.account_label(selected)]
            self.fill('Monthly summary', months)

        def render_scheduled(self, shift):
            b = self.book
            records = []
            for x in b.schedules:
                records.append({'Next date': formatted_date(x.next_timestamp, shift),
                                'Account': b.account_label(x.account_id),
                                'Payee': b.payee_label(x.payee_id),
                                'Category': b.category_label(x.category_id),
                                'Amount': money(x.amount), 'Note': x.fields[0],
                                'Frequency code': x.frequency_code, 'ID': x.id})
            self.fill('Scheduled', records)

        def render_static(self):
            b = self.book
            total_by_account = defaultdict(lambda: Decimal('0'))
            count_by_account = defaultdict(int)
            for t in b.transactions:
                total_by_account[t.account_id] += Decimal(str(t.amount))
                count_by_account[t.account_id] += 1
            accounts = []
            for a in b.accounts:
                bal = Decimal(str(a.opening_balance)) + total_by_account[a.id]
                accounts.append({'ID': a.id, 'Account': a.name,
                                 'Type': b.account_types.get(a.account_type_id,
                                                              f'#{a.account_type_id}'),
                                 'Description': a.comment,
                                 'Bank': b.banks.get(a.bank_id, ''),
                                 'Currency ID': a.currency_id,
                                 'Opening balance': money(a.opening_balance),
                                 'Transactions': count_by_account[a.id],
                                 'Calculated balance': f'{bal:,.2f}'})
            self.fill('Accounts', accounts)
            self.fill('Categories', [{'ID': i, 'Category': name,
                                      'Description': b.category_comments.get(i, '')}
                                     for i, name in b.categories.items()])
            self.fill('Payees', [{'ID': i, 'Payee': name}
                                 for i, name in b.payees.items()])
            details = [f'Original file: {b.source}',
                       f'File size: {b.total_size:,} bytes',
                       f'Bytes decoded through: {b.bytes_read:,}',
                       f'Accounts: {len(b.accounts)}',
                       f'Banks: {len(b.banks)}',
                       f'Categories: {len(b.categories)}',
                       f'Payees: {len(b.payees)}',
                       f'Transactions: {len(b.transactions)}',
                       f'Scheduled payments: {len(b.schedules)}',
                       '',
                       'Important: This is a forensic read-only viewer, not a complete AceMoney clone.',
                       'The amount, payee, category and account IDs follow the supplied sample layout.',
                       'Split allocations, stock transactions,',
                       'exchange rates, budgets and other trailing sections have not been verified.',
                       'Currency IDs are not converted to currency codes.',
                       'Dates initially use your computer’s current UTC offset. If dates appear',
                       'one day out, change the date shift (hours) at the top (India = 5.5).',
                       'Neither original files nor backup files are modified. Exports are new CSV files.',
                       '', 'Warnings:']
            details += ([' • '+w for w in b.warnings] if b.warnings else [' None for parsed sections.'])
            self.details.configure(state='normal')
            self.details.delete('1.0', 'end')
            self.details.insert('1.0', '\n'.join(details))
            self.details.configure(state='disabled')

        def export_transactions(self):
            if not self.book:
                messagebox.showinfo('No file','Open an MMW file first.')
                return
            rows = [x for x in self.tx_cache if self.matches(x)]
            self.save_csv(rows, 'transactions')

        def export_monthly(self):
            if not self.book:
                messagebox.showinfo('No file','Open an MMW file first.')
                return
            selected = self.account_choices.get(self.selected_account.get())
            rows = [x for x in self.month_cache if
                    selected is None or x['Account'] == self.book.account_label(selected)]
            self.save_csv(rows, 'monthly')

        def bulk_export(self):
            sources = filedialog.askopenfilenames(
                title='Select AceMoney files to export (multiple selection)',
                initialdir=str(Path(self.book.source).parent) if self.book else None,
                filetypes=[('AceMoney files', '*.mmw'), ('All files', '*.*')])
            if not sources:
                return
            destination = filedialog.askdirectory(
                title='Choose folder for exported CSV files', mustexist=True)
            if not destination:
                return

            folder = Path(destination)
            existing = [Path(src).stem + '.csv' for src in sources
                        if (folder / (Path(src).stem + '.csv')).exists()]
            overwrite = False
            if existing:
                answer = messagebox.askyesnocancel(
                    'Existing CSV files',
                    f'{len(existing)} CSV filename(s) already exist in that folder.\n\n'
                    'Yes: overwrite those CSV files.\n'
                    'No: skip existing CSV files.\n'
                    'Cancel: stop the bulk export.')
                if answer is None:
                    return
                overwrite = answer

            successes, skips, failures = bulk_export_transactions(
                sources, destination, self.date_shift(), overwrite=overwrite)
            transaction_count = sum(count for _, count in successes)
            empty_count = sum(count == 0 for _, count in successes)
            summary = (f'Exported {len(successes)} CSV file(s) '
                       f'({transaction_count} transactions).\n'
                       f'Skipped: {len(skips)}. Failed: {len(failures)}.\n\n'
                       f'Destination: {destination}')
            if empty_count:
                summary += (f'\n\n{empty_count} file(s) contained no transactions; '
                            'a header-only CSV was created for each.')
            if skips:
                summary += '\n\nSkipped:\n' + '\n'.join(
                    f'• {name}: {reason}' for name, reason in skips[:12])
            if failures:
                summary += '\n\nFailed:\n' + '\n'.join(
                    f'• {name}: {reason}' for name, reason in failures[:12])
            if len(skips) > 12 or len(failures) > 12:
                summary += '\n(Only the first 12 of each issue type are shown.)'
            self.status.set(f'Bulk export: {len(successes)} CSVs · '
                            f'{len(skips)} skipped · {len(failures)} failed')
            if failures:
                messagebox.showwarning('Bulk Export completed with errors', summary)
            else:
                messagebox.showinfo('Bulk Export complete', summary)

        def save_csv(self, rows, suffix):
            if not rows:
                messagebox.showinfo('No rows','No matching records to export.')
                return
            original = Path(self.book.source)
            target = filedialog.asksaveasfilename(
                title='Export CSV (new file)', defaultextension='.csv',
                initialfile=f'{original.stem}_{suffix}.csv',
                filetypes=[('CSV files','*.csv')])
            if target:
                try:
                    csv_export(target, rows, self.book.source)
                    messagebox.showinfo('Export complete',f'Exported {len(rows)} rows to:\n{target}')
                except (OSError, ValueError) as err:
                    messagebox.showerror('CSV export failed',str(err))

    root = tk.Tk()
    Viewer(root)
    root.mainloop()


def main():
    p = argparse.ArgumentParser(description='AceMoney .MMW legacy read-only Python viewer')
    p.add_argument('file', nargs='?', help='MMW file to open')
    p.add_argument('--inspect', action='store_true', help='Show a summary, no GUI')
    p.add_argument('--export-csv', metavar='OUTPUT', help='Export all transactions to CSV, no GUI')
    p.add_argument('--export-monthly', metavar='OUTPUT', help='Export monthly summary to CSV')
    p.add_argument('--date-shift-hours', type=float, default=local_timezone_shift_hours(),
                   help='UTC hour offset for displayed dates (default: computer current offset)')
    args = p.parse_args()
    if args.inspect or args.export_csv or args.export_monthly:
        if not args.file:
            p.error('Provide a .mmw file for command-line inspection/export')
        try:
            book = load_mmw(args.file)
            if args.inspect:
                print(json.dumps({
                    'file': book.source, 'bytes': book.total_size,
                    'accounts': [asdict(a) for a in book.accounts],
                    'bank_count': len(book.banks),
                    'category_count': len(book.categories),
                    'payee_count': len(book.payees),
                    'transaction_count': len(book.transactions),
                    'schedule_count': len(book.schedules),
                    'transactions': list(transaction_rows(book, args.date_shift_hours))[:10],
                    'warnings': book.warnings}, indent=2, ensure_ascii=False))
            if args.export_csv:
                rows = list(transaction_rows(book, args.date_shift_hours))
                csv_export(args.export_csv, rows, args.file)
                print(f'Saved {len(rows)} transactions to {args.export_csv}')
            if args.export_monthly:
                rows = list(monthly_rows(book, args.date_shift_hours))
                csv_export(args.export_monthly, rows, args.file)
                print(f'Saved {len(rows)} monthly rows to {args.export_monthly}')
        except (OSError, ValueError) as err:
            p.exit(1, f'Error: {err}\n')
    else:
        make_gui(args.file)

if __name__ == '__main__':
    main()
