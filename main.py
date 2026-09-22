"""
main.py - the menu loop. Talks to the user, delegates real work to MakerSpace.

Design rule: no SQL and no business rules in here. If a rule changes, we edit
models.py; if a query changes, we edit services.py. This file only asks,
calls, and prints.
"""

import csv
import sqlite3
from services import MakerSpace
from models import MakerSpaceError, Equipment
from colored import Fore, Back, Style

BANNER = r"""
  __  __       _              ____
 |  \/  | __ _| | _____ _ __ / ___| _ __   __ _  ___ ___
 | |\/| |/ _` | |/ / _ \ '__|\___ \| '_ \ / _` |/ __/ _ \
 | |  | | (_| |   <  __/ |    ___) | |_) | (_| | (_|  __/
 |_|  |_|\__,_|_|\_\___|_|   |____/| .__/ \__,_|\___\___|
                                   |_|   Checkout Desk
"""


# --------------------------------------------------------------------------
# Input helpers: every prompt re-asks on bad input, so nothing can crash here.
# --------------------------------------------------------------------------
def ask_text(prompt, required=True, default=None):
    """Free text. `default` is shown so a blank answer can mean 'keep current'."""
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        if not required:
            return ""
        print("  ! This field can't be empty.")


def ask_int(prompt, low=1, high=None, default=None):
    """Whole number in [low, high]."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("  ! Please type a whole number.")
            continue
        if value < low or (high is not None and value > high):
            print(f"  ! Enter a number between {low} and {high if high else 'any'}.")
            continue
        return value


def ask_yes_no(prompt):
    while True:
        raw = input(f"{prompt} (y/n): ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  ! Answer y or n.")


def show_table(headers, rows):
    """Print rows as an aligned text table; tell the user if there's nothing to show."""
    rows = [["" if v is None else str(v) for v in r] for r in rows]
    if not rows:
        print("  (nothing to show)")
        return
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    line = "  ".join("-" * w for w in widths)
    print("  " + "  ".join(h.upper().ljust(w) for h, w in zip(headers, widths)))
    print("  " + line)
    for r in rows:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(r, widths)))
    print(f"  ({len(rows)} row{'s' if len(rows) != 1 else ''})")


def show_members(members):
    show_table(["id", "name", "email", "joined", "active"],
               [(m.id, m.name, m.email, m.joined_on, "yes" if m.is_active else "no")
                for m in members])


def show_equipment(items):
    show_table(["id", "name", "category", "status", "note"],
               [(i.id, i.name, i.category, i.status, i.note) for i in items])


# --------------------------------------------------------------------------
# Menu actions. Each one is a small function so the menu tables stay readable.
# --------------------------------------------------------------------------
def add_member(ms):
    m = ms.add_member(ask_text("Full name"), ask_text("Email"))
    print(f"  + Registered {m}")


def list_members(ms):
    show_members(ms.list_members())


def update_member(ms):
    m = ms.get_member(ask_int("Member ID"))
    print(f"  Editing {m}  (press Enter to keep a value)")
    name = ask_text("Name", default=m.name)
    email = ask_text("Email", default=m.email)
    if m.is_active:
        active = not ask_yes_no("Deactivate this member?")
    else:
        active = ask_yes_no("Reactivate this member?")
    ms.update_member(m.id, name, email, is_active=active)
    print("  ~ Member updated.")


def delete_member(ms):
    m = ms.get_member(ask_int("Member ID to delete"))
    if ask_yes_no(f"Really delete {m.name}?"):
        ms.delete_member(m.id)
        print("  - Member deleted.")


def add_equipment(ms):
    item = ms.add_equipment(ask_text("Item name"), ask_text("Category"),
                            ask_text("Note (optional)", required=False))
    print(f"  + Added {item}")


def list_equipment(ms):
    show_equipment(ms.list_equipment())


def update_equipment(ms):
    item = ms.get_equipment(ask_int("Equipment ID"))
    print(f"  Editing {item}  (press Enter to keep a value)")
    name = ask_text("Name", default=item.name)
    category = ask_text("Category", default=item.category)
    note = ask_text("Note", default=item.note or "", required=False)
    status = ask_text(f"Status {list(Equipment.STATUSES)}", default=item.status).lower()
    ms.update_equipment(item.id, name, category, note, status)
    print("  ~ Equipment updated.")


def delete_equipment(ms):
    item = ms.get_equipment(ask_int("Equipment ID to delete"))
    if ask_yes_no(f"Really delete {item.name}?"):
        ms.delete_equipment(item.id)
        print("  - Equipment deleted.")


def search_members(ms):
    show_members(ms.search_members(ask_text("Search name, email or ID")))


def search_equipment(ms):
    show_equipment(ms.search_equipment(ask_text("Search name, category or ID")))


def checkout(ms):
    print("  Available right now:")
    show_equipment(ms.list_equipment(status="available"))
    member_id = ask_int("Member ID")
    equipment_id = ask_int("Equipment ID")
    days = ask_int("Loan length in days", 1, 30, default=7)
    loan = ms.checkout(member_id, equipment_id, days)
    print(f"  + Loan #{loan.id} created. Due back on {loan.due_on}.")


def return_item(ms):
    print("  Currently out:")
    rows = ms.report_active_loans()
    show_table(["loan", "member", "item", "out", "due"], [tuple(r) for r in rows])
    if not rows:
        return
    loan = ms.return_loan(ask_int("Loan ID to return"))
    late = loan.days_overdue()
    print("  + Returned on time. Thank you!" if not late
          else f"  + Returned {late} day(s) late.")


def renew(ms):
    print("  Currently out:")
    rows = ms.report_active_loans()
    show_table(["loan", "member", "item", "out", "due"], [tuple(r) for r in rows])
    if not rows:
        return
    loan_id = ask_int("Loan ID to renew")
    days = ask_int("Extra days", 1, 14, default=7)
    loan = ms.renew_loan(loan_id, days)
    print(f"  + Loan #{loan.id} renewed. New due date: {loan.due_on}.")


def rep_active(ms):
    show_table(["loan", "member", "item", "out", "due"],
               [tuple(r) for r in ms.report_active_loans()])


def rep_overdue(ms):
    show_table(["loan", "member", "email", "item", "due", "days late"],
               [tuple(r) for r in ms.report_overdue()])


def rep_category(ms):
    show_table(["category", "total", "available", "on loan", "unavailable"],
               [tuple(r) for r in ms.report_equipment_by_category()])


def rep_history(ms):
    member_id = ask_int("Member ID")
    print(f"  History for {ms.get_member(member_id).name}:")
    show_table(["loan", "item", "out", "due", "returned"],
               [tuple(r) for r in ms.report_member_history(member_id)])


def rep_popular(ms):
    show_table(["item", "category", "times borrowed"],
               [tuple(r) for r in ms.report_most_borrowed()])


RELIABILITY_HEADERS = ["member", "on time", "late", "overdue now", "points", "tier", "item limit"]


def rep_reliability(ms):
    print("  On-time +10, late -15, overdue now -5.  Trusted 20+ (5 items), "
          "Standard 0+ (3), Probation (1).")
    show_table(RELIABILITY_HEADERS, ms.report_reliability())


# Each exportable report: label, file name, column headers, function returning rows.
EXPORTABLE = [
    ("Currently borrowed", "active_loans.csv", ["loan", "member", "item", "out", "due"],
     lambda ms: [tuple(r) for r in ms.report_active_loans()]),
    ("Overdue loans", "overdue_loans.csv",
     ["loan", "member", "email", "item", "due", "days late"],
     lambda ms: [tuple(r) for r in ms.report_overdue()]),
    ("Stock by category", "stock_by_category.csv",
     ["category", "total", "available", "on loan", "unavailable"],
     lambda ms: [tuple(r) for r in ms.report_equipment_by_category()]),
    ("Maker reliability", "reliability.csv", RELIABILITY_HEADERS,
     lambda ms: ms.report_reliability()),
]


def export_report(ms):
    for n, (label, filename, _, _) in enumerate(EXPORTABLE, 1):
        print(f"  {n}. {label}  ->  {filename}")
    _, filename, headers, fetch = EXPORTABLE[ask_int("Which report", 1, len(EXPORTABLE)) - 1]
    rows = fetch(ms)
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    print(f"  + Saved {len(rows)} row(s) to {filename}")


def sample_data(ms):
    ms.load_sample_data()
    print("  + Sample makerspace loaded (4 members, 8 items, a mix of loan history).")


# Menus are data: (label, function). Adding a feature = adding one line.
MEMBER_MENU = [("Register member", add_member), ("List members", list_members),
               ("Update member", update_member), ("Delete member", delete_member),
               ("Search members", search_members)]
EQUIPMENT_MENU = [("Register equipment", add_equipment), ("List equipment", list_equipment),
                  ("Update equipment / status", update_equipment),
                  ("Delete equipment", delete_equipment),
                  ("Search equipment", search_equipment)]
LOAN_MENU = [("Check out an item", checkout), ("Return an item", return_item),
             ("Renew a loan", renew)]
REPORT_MENU = [("Currently borrowed items", rep_active), ("Overdue loans", rep_overdue),
               ("Stock by category", rep_category), ("Member loan history", rep_history),
               ("Most borrowed equipment", rep_popular),
               ("Maker reliability scores", rep_reliability),
               ("Export a report to CSV", export_report)]
MAIN_MENU = [("Members", MEMBER_MENU), ("Equipment", EQUIPMENT_MENU),
             ("Loans", LOAN_MENU), ("Reports", REPORT_MENU),
             ("Load sample data", sample_data)]


def run_menu(title, options, ms):
    """Generic menu loop used for the main menu AND every submenu."""
    while True:
        print(f"\n{Fore.white}{Back.green}==== {title} ===={Style.reset}")
        for n, (label, _) in enumerate(options, 1):
            print(f"  {n}. {label}")
        print("  0. Back" if title != "MAIN MENU" else "  0. Quit")
        choice = ask_int("Choose", 0, len(options))
        if choice == 0:
            return
        label, action = options[choice - 1]
        if isinstance(action, list):                # a submenu
            run_menu(label.upper(), action, ms)
            continue
        try:
            print()
            action(ms)
        except MakerSpaceError as err:              # our own, expected problems
            print(f"  ! {err}")
        except sqlite3.Error as err:                # anything the database objects to
            print(f"  ! Database problem: {err}")
        except OSError as err:                      # e.g. CSV file is open elsewhere
            print(f"  ! Could not write the file: {err}")
        except KeyboardInterrupt:                   # Ctrl+C cancels the action, not the app
            print("\n  (cancelled)")
        print("--------------------------------------------------------------------------")

def main():
    print(f"{Fore.white}{Back.red}{BANNER}{Style.reset}")
    ms = MakerSpace()
    try:
        run_menu("MAIN MENU", MAIN_MENU, ms)
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        ms.db.close()
        print("Goodbye - happy making!")


if __name__ == "__main__":
    main()
