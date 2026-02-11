from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    salary_credit_day = db.Column(db.Integer, nullable=False, default=8)
    budgets = db.relationship("MonthlyBudget", backref="user", lazy=True)
    transactions = db.relationship("Transaction", backref="user", lazy=True)


class MonthlyBudget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    month_start_date = db.Column(
        db.Date, nullable=False
    )  # The 8th of the month this budget starts
    salary_credited = db.Column(db.Float, nullable=False)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow().date)
    time = db.Column(db.Time, nullable=False, default=lambda: datetime.utcnow().time())
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200), nullable=False)  # "Money spent on"
