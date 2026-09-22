"""
services.py - the "front desk" that makes models and the database cooperate.

Pattern for every operation:
    1. load rows -> build model objects
    2. let the MODEL enforce the rules (can_borrow, check_out, close, ...)
    3. save the result, using a transaction when several writes must go together
"""

import sqlite3
from datetime import date, timedelta

from database import Database
from models import (Member, Equipment, Loan,
                    ValidationError, NotFoundError, RuleViolation)

# Reused SELECT so every equipment query returns the category *name*, not just its id.
_EQUIPMENT_SELECT = """
    SELECT e.id, e.name, c.name AS category, e.status, e.note
    FROM equipment e JOIN categories c ON c.id = e.category_id
"""


class MakerSpace:
    """Application service: every menu option maps to one method here."""

    def __init__(self, db=None):
        self.db = db or Database()

    # ======================= MEMBERS =========================================================
    def add_member(self, name, email):
        member = Member(name, email)                       # validates
        try:
            member.id = self.db.execute(
                "INSERT INTO members (name, email, joined_on, is_active) VALUES (?,?,?,1)",
                (member.name, member.email, member.joined_on))
        except sqlite3.IntegrityError:
            raise ValidationError(f"The email {member.email} is already registered.")
        return member

    def get_member(self, member_id):
        row = self.db.query_one("SELECT * FROM members WHERE id = ?", (member_id,))
        if row is None:
            raise NotFoundError("member", member_id)
        return Member.from_row(row)

    def list_members(self):
        return [Member.from_row(r) for r in
                self.db.query("SELECT * FROM members ORDER BY name")]

    def update_member(self, member_id, name=None, email=None, is_active=None):
        member = self.get_member(member_id)
        if name:
            member.name = name
        if email:
            member.email = email
        if is_active is not None:
            member.is_active = is_active
        try:
            self.db.execute(
                "UPDATE members SET name=?, email=?, is_active=? WHERE id=?",
                (member.name, member.email, int(member.is_active), member.id))
        except sqlite3.IntegrityError:
            raise ValidationError(f"The email {member.email} belongs to someone else.")
        return member

    def delete_member(self, member_id):
        """Hard-delete only when the member has no history; otherwise keep the audit trail."""
        member = self.get_member(member_id)
        history = self.db.query_one(
            "SELECT COUNT(*) AS n FROM loans WHERE member_id = ?", (member_id,))["n"]
        if history:
            raise RuleViolation(f"{member.name} has {history} loan record(s) on file. "
                                "Deactivate them instead of deleting.")
        self.db.execute("DELETE FROM members WHERE id = ?", (member_id,))
        return member

    def search_members(self, term):
        """Match by exact ID (if numeric) or partial name / email."""
        like = f"%{term.strip()}%"
        as_id = int(term) if term.strip().isdigit() else -1
        rows = self.db.query(
            "SELECT * FROM members WHERE id = ? OR name LIKE ? OR email LIKE ? ORDER BY name",
            (as_id, like, like))
        return [Member.from_row(r) for r in rows]

    # ======================= EQUIPMENT =========================================================
    def _category_id(self, name):
        """Find a category, creating it on the fly the first time it's used."""
        self.db.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
        return self.db.query_one("SELECT id FROM categories WHERE name = ?", (name,))["id"]

    def add_equipment(self, name, category, note=""):
        item = Equipment(name, category, note=note)        # validates
        item.id = self.db.execute(
            "INSERT INTO equipment (name, category_id, status, note) VALUES (?,?,?,?)",
            (item.name, self._category_id(item.category), item.status, item.note))
        return item

    def get_equipment(self, equipment_id):
        row = self.db.query_one(_EQUIPMENT_SELECT + " WHERE e.id = ?", (equipment_id,))
        if row is None:
            raise NotFoundError("equipment", equipment_id)
        return Equipment.from_row(row)

    def list_equipment(self, status=None):
        sql, params = _EQUIPMENT_SELECT, ()
        if status:
            sql, params = sql + " WHERE e.status = ?", (status,)
        return [Equipment.from_row(r) for r in
                self.db.query(sql + " ORDER BY c.name, e.name", params)]

    def update_equipment(self, equipment_id, name=None, category=None,
                         note=None, status=None):
        item = self.get_equipment(equipment_id)
        # Rebuild through the constructor so all validation runs again.
        edited = Equipment(name or item.name, category or item.category,
                           item.status, item.note if note is None else note, item.id)
        if status:
            edited.change_status(status)                   # state-machine rules
        self.db.execute(
            "UPDATE equipment SET name=?, category_id=?, status=?, note=? WHERE id=?",
            (edited.name, self._category_id(edited.category),
             edited.status, edited.note, edited.id))
        return edited

    def delete_equipment(self, equipment_id):
        item = self.get_equipment(equipment_id)
        history = self.db.query_one(
            "SELECT COUNT(*) AS n FROM loans WHERE equipment_id = ?", (equipment_id,))["n"]
        if history:
            raise RuleViolation(f"'{item.name}' has loan history. Retire it instead of deleting.")
        self.db.execute("DELETE FROM equipment WHERE id = ?", (equipment_id,))
        return item

    def search_equipment(self, term):
        like = f"%{term.strip()}%"
        as_id = int(term) if term.strip().isdigit() else -1
        rows = self.db.query(
            _EQUIPMENT_SELECT + " WHERE e.id = ? OR e.name LIKE ? OR c.name LIKE ? "
                                "ORDER BY e.name", (as_id, like, like))
        return [Equipment.from_row(r) for r in rows]

    # ======================= LOANS ===================================================================
    def _open_loan_count(self, member_id):
        return self.db.query_one(
            "SELECT COUNT(*) AS n FROM loans WHERE member_id = ? AND returned_on IS NULL",
            (member_id,))["n"]

    def checkout(self, member_id, equipment_id, days=None, today=None):
        """Lend an item. Objects decide if it's allowed; the transaction makes it atomic."""
        member = self.get_member(member_id)
        item = self.get_equipment(equipment_id)

        points = self.member_points(member_id, today)
        allowed, reason = member.can_borrow(self._open_loan_count(member_id), points)
        if not allowed:
            raise RuleViolation(reason)
        item.check_out()                                   # raises if unavailable
        loan = Loan.start(member.id, item.id, days, today)

        with self.db.transaction() as conn:                # both writes or neither
            cur = conn.execute(
                "INSERT INTO loans (member_id, equipment_id, loaned_on, due_on) VALUES (?,?,?,?)",
                (loan.member_id, loan.equipment_id, loan.loaned_on, loan.due_on))
            loan.id = cur.lastrowid
            conn.execute("UPDATE equipment SET status = ? WHERE id = ?", (item.status, item.id))
        return loan

    def _get_loan(self, loan_id):
        row = self.db.query_one("SELECT * FROM loans WHERE id = ?", (loan_id,))
        if row is None:
            raise NotFoundError("loan", loan_id)
        return Loan.from_row(row)

    def renew_loan(self, loan_id, days=None, today=None):
        """Extend a loan. The Loan object decides if it's allowed."""
        loan = self._get_loan(loan_id)
        loan.renew(days, today)
        self.db.execute("UPDATE loans SET due_on = ?, renewals = ? WHERE id = ?",
                        (loan.due_on, loan.renewals, loan.id))
        return loan

    def return_loan(self, loan_id, today=None):
        loan = self._get_loan(loan_id)
        item = self.get_equipment(loan.equipment_id)

        loan.close(today)                                  # raises if already returned
        item.check_in()
        with self.db.transaction() as conn:
            conn.execute("UPDATE loans SET returned_on = ? WHERE id = ?",
                         (loan.returned_on, loan.id))
            conn.execute("UPDATE equipment SET status = ? WHERE id = ?", (item.status, item.id))
        return loan

    # ======================= MAKER RELIABILITY ========================================================
    def _reliability_rows(self, today=None, member_id=None):
        """One SQL pass counts on-time / late / overdue loans per member;
        Member turns the counts into points and a tier (rules stay in the model)."""
        today = (today or date.today()).isoformat()
        sql = """
            SELECT m.id, m.name,
              COALESCE(SUM(l.returned_on IS NOT NULL AND l.returned_on <= l.due_on), 0) AS on_time,
              COALESCE(SUM(l.returned_on IS NOT NULL AND l.returned_on >  l.due_on), 0) AS late,
              COALESCE(SUM(l.returned_on IS NULL     AND l.due_on < ?), 0)              AS overdue_now
            FROM members m LEFT JOIN loans l ON l.member_id = m.id"""
        params = [today]
        if member_id is not None:
            sql += " WHERE m.id = ?"
            params.append(member_id)
        rows = self.db.query(sql + " GROUP BY m.id ORDER BY m.name", params)
        result = []
        for r in rows:
            points = Member.reliability_points(r["on_time"], r["late"], r["overdue_now"])
            tier, limit = Member.tier_for(points)
            result.append({"id": r["id"], "name": r["name"], "on_time": r["on_time"],
                           "late": r["late"], "overdue_now": r["overdue_now"],
                           "points": points, "tier": tier, "limit": limit})
        return result

    def member_points(self, member_id, today=None):
        return self._reliability_rows(today, member_id)[0]["points"]

    def report_reliability(self, today=None):
        """Leaderboard of members with their tier and borrowing limit."""
        rows = sorted(self._reliability_rows(today), key=lambda r: -r["points"])
        return [(r["name"], r["on_time"], r["late"], r["overdue_now"],
                 r["points"], r["tier"], r["limit"]) for r in rows]

    # ======================= REPORTS (pure SQL) ==============================
    def report_active_loans(self):
        """Everything currently out, soonest-due first."""
        return self.db.query("""
            SELECT l.id AS loan_id, m.name AS member, e.name AS item,
                   l.loaned_on, l.due_on
            FROM loans l
            JOIN members m   ON m.id = l.member_id
            JOIN equipment e ON e.id = l.equipment_id
            WHERE l.returned_on IS NULL
            ORDER BY l.due_on""")

    def report_overdue(self, today=None):
        """Open loans past their due date, with how late they are."""
        today = (today or date.today()).isoformat()
        return self.db.query("""
            SELECT l.id AS loan_id, m.name AS member, m.email, e.name AS item, l.due_on,
                   CAST(julianday(?) - julianday(l.due_on) AS INTEGER) AS days_late
            FROM loans l
            JOIN members m   ON m.id = l.member_id
            JOIN equipment e ON e.id = l.equipment_id
            WHERE l.returned_on IS NULL AND l.due_on < ?
            ORDER BY days_late DESC""", (today, today))

    def report_equipment_by_category(self):
        """Stock overview: how many items per category, and how many are free right now."""
        return self.db.query("""
            SELECT c.name AS category,
                   COUNT(e.id) AS total,
                   SUM(e.status = 'available')   AS available,
                   SUM(e.status = 'on_loan')     AS on_loan,
                   SUM(e.status IN ('maintenance','retired')) AS unavailable
            FROM categories c LEFT JOIN equipment e ON e.category_id = c.id
            GROUP BY c.id ORDER BY total DESC, c.name""")

    def report_member_history(self, member_id):
        self.get_member(member_id)                         # friendly error if missing
        return self.db.query("""
            SELECT l.id AS loan_id, e.name AS item, l.loaned_on, l.due_on,
                   COALESCE(l.returned_on, 'still out') AS returned
            FROM loans l JOIN equipment e ON e.id = l.equipment_id
            WHERE l.member_id = ? ORDER BY l.loaned_on DESC, l.id DESC""", (member_id,))

    def report_most_borrowed(self, limit=5):
        """Leaderboard of the most popular gear."""
        return self.db.query("""
            SELECT e.name AS item, c.name AS category, COUNT(l.id) AS times_borrowed
            FROM equipment e
            JOIN categories c ON c.id = e.category_id
            JOIN loans l ON l.equipment_id = e.id
            GROUP BY e.id ORDER BY times_borrowed DESC, e.name LIMIT ?""", (limit,))

    # ======================= SAMPLE DATA ====================================================
    def load_sample_data(self):
        """Fill an empty database with a small, realistic makerspace. Safe to call twice."""
        if self.db.query_one("SELECT COUNT(*) AS n FROM members")["n"]:
            raise RuleViolation("Database already has data - sample data skipped.")
        people = [("Amara Okafor", "amara@alu.edu"), ("Liam Chen", "liam@alu.edu"),
                  ("Sofia Reyes", "sofia@alu.edu"), ("Kwame Mensah", "kwame@alu.edu")]
        gear = [("Prusa MK4 Nozzle Kit", "3D Printing"), ("Filament Dryer", "3D Printing"),
                ("Soldering Station", "Electronics"), ("Multimeter", "Electronics"),
                ("Arduino Starter Kit", "Electronics"), ("Mirrorless Camera", "Photography"),
                ("Tripod", "Photography"), ("Loan Laptop", "Computing")]
        for n, e in people:
            self.add_member(n, e)
        for n, c in gear:
            self.add_equipment(n, c)


        today = date.today()

        self.checkout(1, 3, days=7, today=today - timedelta(days=12))   # overdue
        self.checkout(2, 6, days=7, today=today - timedelta(days=2))    # on time
        old = self.checkout(3, 4, days=5, today=today - timedelta(days=20))
        self.return_loan(old.id, today=today - timedelta(days=16))      # history
        self.checkout(3, 4, days=7, today=today - timedelta(days=10))   # borrowed again
        for k in range(3):                                              # Liam: a reliable maker
            trip = self.checkout(2, 7, days=7, today=today - timedelta(days=40 - 6 * k))
            self.return_loan(trip.id, today=today - timedelta(days=38 - 6 * k))
