# Campus MakerSpace Checkout System

A menu-driven Python CLI for a student makerspace. Operators register members and
equipment, lend and return gear, search records and run SQL reports. Data lives in a
SQLite file (`makerspace.db`) that is created automatically on first run.

## Run it
```bash
python main.py          # Python 3.9+; no installs needed
```
Pick **Load sample data** from the main menu for a ready-made demo (includes an overdue loan).

## Project layout
| File | Responsibility |
|------|----------------|
| `main.py` | Menu loop, input helpers, table printing. No SQL, no rules. |
| `models.py` | `Member`, `Equipment`, `Loan` classes + custom exceptions. Business rules only. |
| `database.py` | `Database` class: connection, schema, transactions. All raw SQLite lives here. |
| `services.py` | `MakerSpace` class: loads models, calls their methods, saves results, runs reports. |

## Class design
- **Record** – parent class holding the shared database `id`. `Member`, `Equipment` and `Loan` inherit from it and call `super().__init__()`. `NotFoundError` also uses `super()` to build its message.
- **Member** – validated `name`/`email` (properties). Owns the **Maker Reliability** policy: `reliability_points()`, `tier_for()` and `can_borrow(open_count, points)`.
- **Equipment** – status state machine (`available → on_loan → available`, plus `maintenance`/`retired`); `check_out()`, `check_in()`, `change_status()`.
- **Loan** – `Loan.start()` factory, `close()`, `renew()`, `is_overdue()`, `days_overdue()`.
- **MakerSpace (service)** – collaborates with all three plus `Database`; each menu option is one method.
- **Exceptions** – `ValidationError`, `NotFoundError`, `RuleViolation` all inherit `MakerSpaceError`, so the menu shows a friendly message instead of crashing.

## Database design
```
members(id, name, email UNIQUE, joined_on, is_active)
categories(id, name UNIQUE)
equipment(id, name, category_id -> categories, status CHECK, note)
loans(id, member_id -> members, equipment_id -> equipment, loaned_on, due_on, returned_on NULL=open, renewals)
```
- Categories are a separate table so the name isn't repeated on every item.
- Foreign keys are enabled and `CHECK` constraints back up the Python validation.
- Checkout and return use a **transaction**: the loan row and the equipment status change together or not at all.
- `Database._migrate()` adds the `renewals` column to older database files automatically.
- Members/equipment with loan history cannot be hard-deleted (audit trail) – deactivate or retire them instead.

## Reports (SQL)
Currently borrowed (3-table JOIN) · Overdue loans (`julianday` date math) · Stock by category (`GROUP BY` + conditional `SUM`) · Member loan history · Most borrowed equipment · Maker reliability scores (conditional aggregates per member). Every main report can be exported to CSV.

## Extra features
- **Maker Reliability tiers:** on-time return +10, late return -15, currently overdue -5. Trusted (20+) may hold 5 items, Standard (0+) 3, Probation (below 0) 1. The tier is recomputed from loan history at every checkout.
- **Loan renewals:** one renewal per loan, 1-14 extra days, never on an overdue loan.
- **CSV export** of reports (Reports > Export a report to CSV).

## Validation highlights
Empty/short names, malformed or duplicate emails, unknown IDs, unavailable or maintenance items,
inactive members, tier-based item limits, renewal rules, loan length 1–30 days, non-numeric menu input, Ctrl+C mid-action, EOF.

## AI disclosure
<!-- EDIT THIS to be truthful. Example: -->
I used [tool name] to [what it helped with, e.g. discuss the schema design, debug X, review my README].
I have read, understood and modified the code, and can explain every part of it.

## References
- Python Software Foundation. (2026). *sqlite3 — DB-API 2.0 interface for SQLite databases*. https://docs.python.org/3/library/sqlite3.html
