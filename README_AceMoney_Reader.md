# AceMoney Legacy Reader — Python v1.2

A **read-only, offline** macOS/Python viewer and CSV exporter for old AceMoney `.mmw` files. It never launches old Windows executables, changes an `.mmw`, or depends on a separate `.mmw` template. Written using Python's standard library (including Tkinter for the GUI).

## Quick start (Mac)

1. Unzip the package and open Terminal in the extracted directory.
2. Run `python3 acemoney_reader.py`.
3. Click **Open .MMW…** and choose a **copy** of any old AceMoney file.
4. Explore the **Transactions**, **Monthly summary**, **Scheduled**, **Accounts**, **Categories**, **Payees**, and **File details** tabs.
5. Click **Export transactions CSV…** or **Export monthly CSV…** to save extracted data as new files.

To run directly with a file: `python3 acemoney_reader.py "/path/to/Apr09(1).mmw"`

### Dates and time zone

The stored Unix timestamp represents a date at local midnight in the originating Windows/AceMoney timezone. For example, an April 1, 2009 transaction stored as `2009-03-31 18:30 UTC` corresponds to April 1 in India (UTC+05:30). Earlier versions displayed these records one day early when set to UTC. **v1.2 initially selects your computer's current UTC offset**, but you can change **Date shift (hours)** in the toolbar, using **5.5** for India. For a file made in a different timezone, select that timezone's historical offset. Historical daylight-saving offsets may differ from today's.

## Commands

```bash
python3 acemoney_reader.py --inspect "Apr09(1).mmw"
python3 acemoney_reader.py "Apr09(1).mmw" --export-csv april_transactions.csv
python3 acemoney_reader.py "Apr09(1).mmw" --export-monthly april_monthly.csv
python3 acemoney_reader.py "Apr09(1).mmw" --inspect --date-shift-hours 5.5
python3 -m unittest -v test_acemoney_reader.py
```

## What we decoded and verified

| File | Accounts | Banks | Categories | Transactions | Scheduled |
|---|---:|---:|---:|---:|---:|
| sample.mmw (bundled example) | 1 | 207 | 120 | 5 | 4 |
| IVF.mmw (supplied separately) | 1 | 2 | 16 | 30 | 0 |
| Model Jan-Dec09.mmw (supplied separately) | 1 | 2 | 201 | 0 | 0 |
| Apr09(1).mmw (supplied separately) | 1 | 2 | 198 | 120 | 0 |

The **Model** file resembles a reusable template: it has 201 categories (including descriptions) and no posted transactions. **Apr09(1)** has its own account, category records, and 120 transactions. The model file is not required to open April.

## v1.2 fix: category description is not a flag

The legacy category layout is `ID (u32), name (CString), description (CString)`; for categories without descriptions, the final field is one zero byte. Earlier parser versions mistook it for a one-byte flag. Many of the 2009 categories have descriptions longer than one byte, causing misalignment and `Implausible payees count` errors. v1.2 decodes the full description and displays it in the **Categories** tab, and also performs ID/name validation to avoid a false “success” on garbled records.

`test_acemoney_reader.py` includes regressions for a synthetic long category description, invalid category IDs, and optional verification of the user's 2009/2010 files when those files are placed beside the script. The private `.mmw` files are **not included in this ZIP**.

## Limitations / precautions

- This is a **recovery viewer**, not a complete AceMoney implementation. It does not write `.mmw` files or change original files.
- Some sections after the scheduled entries (split allocations, investment information, price history, currencies, budgets, application preferences) are not fully decoded. Review warnings in **File details**.
- Amounts are read as stored in the binary file; currency conversion and reconciliation have not been verified. Derived balances should not be used for tax/accounting decisions without independent confirmation.
- The file may have been saved under a different historic Windows encoding; some international text may be imperfectly decoded.
- Protected files or different AceMoney generations may still need support.
- Before analyzing an entire archive, compare several known individual transactions with original account records, including dates, categories, and amounts.
