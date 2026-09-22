"""
models.py is the domain classes with Member, Equipment and Loan.

These classes know the RULES of a makerspace (who may borrow, what state an
item can move to, when a loan is late) but know nothing about SQL. The
services layer loads them from rows, calls their methods, and saves them back.
"""

import re
from datetime import date, timedelta


# ***************************************************************************
# One small exception family, so the menu can catch a single base class and
# show a friendly message instead of a traceback.
# ***************************************************************************
class MakerSpaceError(Exception):
    """Base class for every 'expected' problem in the app."""


class ValidationError(MakerSpaceError):
    """Bad user input (empty name, malformed email, ...)."""


class NotFoundError(MakerSpaceError):
    """A record the user asked for does not exist."""

    def __init__(self, kind, record_id):
        # super() hands the finished message to the parent Exception class,
        # so callers only say WHAT is missing: NotFoundError("member", 7)
        super().__init__(f"No {kind} with ID {record_id}.")
        self.kind = kind
        self.record_id = record_id


class RuleViolation(MakerSpaceError):
    """The input was valid, but the makerspace rules forbid the action."""


class Record:
    """
    Parent class for everything stored in the database.
    Member, Equipment and Loan all have a database id, so that shared piece
    lives here once and each child reuses it through super().__init__().
    """

    def __init__(self, record_id=None):
        self.id = record_id            # None until the row is saved

    def is_saved(self):
        """True once the database has given this object an id."""
        return self.id is not None


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NAME_PATTERN = re.compile(r"^[^@\s\d]+\s[^@\s\d]+$")


class Member(Record):
    """A person allowed to borrow equipment."""

    # ****** Maker Reliability system **********************************************
    # Policy lives here, not scattered through the menu. Change a number and the
    # whole app follows.
    PTS_ON_TIME = 10    # returned on or before the due date
    PTS_LATE = -15      # returned after the due date
    PTS_OVERDUE = -5    # still out and already past due
    # (minimum points, tier name, max items at once) - checked top to bottom
    TIERS = ((20, "Trusted", 5), (0, "Standard", 3), (float("-inf"), "Probation", 1))

    def __init__(self, name, email, member_id=None, joined_on=None, is_active=True):
        super().__init__(member_id)   # parent sets self.id
        self.name = name              # goes through the validating setter below
        self.email = email
        self.joined_on = joined_on or date.today().isoformat()
        self.is_active = bool(is_active)

    # Properties give us encapsulation: an invalid name can never be stored.
    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        value = (value or "").strip()
        if len(value) < 2:
            raise ValidationError("Name must be at least 2 characters.")
        print(value)
        if not NAME_PATTERN.match(value):
            raise ValidationError("That doesn't look like a valid name, therefore can't be saved!")
        self._name = value

    @property
    def email(self):
        return self._email

    @email.setter
    def email(self, value):
        value = (value or "").strip().lower()
        print(value)
        if not EMAIL_PATTERN.match(value):
            raise ValidationError("That doesn't look like a valid email address, therefore can't be saved!")
        self._email = value

    @classmethod
    def reliability_points(cls, on_time, late, overdue_now):
        """Turn a loan-history summary into a single score."""
        return (on_time * cls.PTS_ON_TIME + late * cls.PTS_LATE
                + overdue_now * cls.PTS_OVERDUE)

    @classmethod
    def tier_for(cls, points):
        """Return (tier name, item limit) for a score."""
        for minimum, name, limit in cls.TIERS:
            if points >= minimum:
                return name, limit

    def can_borrow(self, open_loan_count, points=0):
        """Return (allowed, reason). The service supplies the loan count and score."""
        if not self.is_active:
            return False, f"{self.name} is deactivated and cannot borrow."
        tier, limit = self.tier_for(points)
        if open_loan_count >= limit:
            return False, (f"{self.name} already holds {open_loan_count} item(s); "
                           f"{tier} members may hold {limit} ({points} pts).")
        return True, ""

    @classmethod
    def from_row(cls, row):
        """Build a Member from a database row."""
        return cls(row["name"], row["email"], row["id"], row["joined_on"], row["is_active"])

    def __str__(self):
        flag = "" if self.is_active else " [inactive]"
        return f"#{self.id} {self.name} <{self.email}>{flag}"

ITEM_PATTERN = re.compile(r"^[^@\s\d]+")

class Equipment(Record):
    """One physical item on the shelf."""

    STATUSES = ("available", "on_loan", "maintenance", "retired")

    # A tiny state machine: which statuses may an item move to from where it is?
    # 'on_loan' is deliberately absent from manual moves only check_out() does that.
    _ALLOWED_MOVES = {
        "available":   {"maintenance", "retired"},
        "on_loan":     set(),                       # must be returned first
        "maintenance": {"available", "retired"},
        "retired":     set(),
    }

    def __init__(self, name, category, status="available", note="", equipment_id=None):
        name = (name or "").strip()
        category = (category or "").strip().title()
        if len(name) < 2:
            raise ValidationError("Equipment name must be at least 2 characters.")
        if not category:
            raise ValidationError("Category cannot be empty.")
        if status not in self.STATUSES:
            raise ValidationError(f"Status must be one of: {', '.join(self.STATUSES)}.")
        super().__init__(equipment_id)   # parent sets self.id
        self.name = name
        self.category = category
        self.status = status
        self.note = (note or "").strip()

    def is_available(self):
        return self.status == "available"

    def check_out(self):
        if not self.is_available():
            raise RuleViolation(f"'{self.name}' is not available (status: {self.status}).")
        self.status = "on_loan"

    def check_in(self):
        if self.status != "on_loan":
            raise RuleViolation(f"'{self.name}' is not currently on loan.")
        self.status = "available"

    def change_status(self, new_status):
        """Manual status change for staff (maintenance, retire, back to shelf)."""
        if new_status not in self.STATUSES:
            raise ValidationError(f"Status must be one of: {', '.join(self.STATUSES)}.")
        if new_status == self.status:
            return
        if new_status not in self._ALLOWED_MOVES[self.status]:
            hint = " Return the loan first." if self.status == "on_loan" else ""
            raise RuleViolation(f"Cannot move '{self.name}' from {self.status} "
                                f"to {new_status}.{hint}")
        self.status = new_status
    @property
    def name(self):
        return self._name
    
    @name.setter
    def name(self, value):
        # print(ITEM_PATTERN.match(value))
        if not ITEM_PATTERN.match(value):
            raise ValidationError("That doesn't look like a valid item name, therefore can't be saved!")
        self._name = value

    @classmethod
    def from_row(cls, row):
        """Expects a row that already joined the category name as 'category'."""
        return cls(row["name"], row["category"], row["status"], row["note"], row["id"])

    def __str__(self):
        return f"#{self.id} {self.name} ({self.category}) - {self.status}"


class Loan(Record):
    """A record that one member has or had one item."""

    DEFAULT_LOAN_DAYS = 7
    MAX_LOAN_DAYS = 30
    MAX_RENEWALS = 1
    MAX_RENEWAL_DAYS = 14

    def __init__(self, member_id, equipment_id, loaned_on, due_on,
                 returned_on=None, loan_id=None, renewals=0):
        super().__init__(loan_id)        # parent sets self.id
        self.member_id = member_id
        self.equipment_id = equipment_id
        self.loaned_on = loaned_on
        self.due_on = due_on
        self.returned_on = returned_on
        self.renewals = renewals

    @classmethod
    def start(cls, member_id, equipment_id, days=None, today=None):
        """Factory: open a brand-new loan due `days` from `today`."""
        days = cls.DEFAULT_LOAN_DAYS if days is None else days
        if not 1 <= days <= cls.MAX_LOAN_DAYS:
            raise ValidationError(f"Loan length must be 1-{cls.MAX_LOAN_DAYS} days.")
        today = today or date.today()
        return cls(member_id, equipment_id, today.isoformat(),
                   (today + timedelta(days=days)).isoformat())

    @property
    def is_open(self):
        return self.returned_on is None

    def days_overdue(self, today=None):
        """0 if on time, otherwise how many days past due."""
        today = today or date.today()
        end = date.fromisoformat(self.returned_on) if self.returned_on else today
        return max(0, (end - date.fromisoformat(self.due_on)).days)

    def is_overdue(self, today=None):
        return self.is_open and self.days_overdue(today) > 0

    def close(self, today=None):
        if not self.is_open:
            raise RuleViolation("This loan has already been returned.")
        self.returned_on = (today or date.today()).isoformat()

    def renew(self, days=None, today=None):
        """Push the due date back. Only open, not-yet-late loans, and only once."""
        if not self.is_open:
            raise RuleViolation("Only loans that are still out can be renewed.")
        if self.is_overdue(today):
            raise RuleViolation("This loan is already overdue - return it instead.")
        if self.renewals >= self.MAX_RENEWALS:
            raise RuleViolation(f"This loan was already renewed "
                                f"(limit is {self.MAX_RENEWALS}).")
        days = self.DEFAULT_LOAN_DAYS if days is None else days
        if not 1 <= days <= self.MAX_RENEWAL_DAYS:
            raise ValidationError(f"Renewal must be 1-{self.MAX_RENEWAL_DAYS} days.")
        # Extend from the current due date, not from today.
        new_due = date.fromisoformat(self.due_on) + timedelta(days=days)
        self.due_on = new_due.isoformat()
        self.renewals += 1

    @classmethod
    def from_row(cls, row):
        return cls(row["member_id"], row["equipment_id"], row["loaned_on"],
                   row["due_on"], row["returned_on"], row["id"], row["renewals"])
