import datetime
import io
import os

from dotenv import load_dotenv
from flask import (Flask, flash, redirect, render_template, request, send_file,
                   url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from werkzeug.security import check_password_hash, generate_password_hash

from models import MonthlyBudget, Transaction, User, db

load_dotenv()

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-for-now")

# Use DATABASE_URL from Vercel/Production, fallback to local sqlite
database_url = os.environ.get("DATABASE_URL", "sqlite:///balance.db")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
    
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def get_current_cycle_start(day=8, today=None):
    if today is None:
        today = datetime.date.today()

    import calendar

    def get_actual_day(y, m, d):
        _, last_day = calendar.monthrange(y, m)
        return min(d, last_day)

    # Current month's target day
    current_target_day = get_actual_day(today.year, today.month, day)

    if today.day >= current_target_day:
        return datetime.date(today.year, today.month, current_target_day)
    else:
        # Go back to previous month
        first_of_this_month = datetime.date(today.year, today.month, 1)
        last_of_prev_month = first_of_this_month - datetime.timedelta(days=1)
        prev_target_day = get_actual_day(
            last_of_prev_month.year, last_of_prev_month.month, day
        )
        return datetime.date(
            last_of_prev_month.year, last_of_prev_month.month, prev_target_day
        )


@app.route("/")
@login_required
def index():
    cycle_start = get_current_cycle_start(current_user.salary_credit_day)
    budget = MonthlyBudget.query.filter_by(
        user_id=current_user.id, month_start_date=cycle_start
    ).first()

    if not budget:
        return redirect(url_for("set_budget"))

    # Calculate balance
    transactions = (
        Transaction.query.filter(
            Transaction.user_id == current_user.id, Transaction.date >= cycle_start
        )
        .order_by(Transaction.date.desc(), Transaction.time.desc())
        .all()
    )

    total_spent = sum(t.amount for t in transactions)
    remaining_balance = budget.salary_credited - total_spent

    return render_template(
        "index.html",
        budget=budget,
        transactions=transactions,
        total_spent=total_spent,
        remaining_balance=remaining_balance,
        cycle_start=cycle_start,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("index"))
        flash("Invalid username or password")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        salary = request.form.get("salary")
        billing_cycle = request.form.get("billing_cycle")

        if User.query.filter_by(username=username).first():
            flash("Username already exists")
            return redirect(url_for("register"))

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            salary_credit_day=int(billing_cycle),
        )
        db.session.add(user)
        db.session.commit()

        # Set initial budget
        cycle_start = get_current_cycle_start(int(billing_cycle))
        budget = MonthlyBudget(
            user_id=user.id, month_start_date=cycle_start, salary_credited=float(salary)
        )
        db.session.add(budget)
        db.session.commit()

        login_user(user)
        return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/set_budget", methods=["GET", "POST"])
@login_required
def set_budget():
    cycle_start = get_current_cycle_start(current_user.salary_credit_day)
    if request.method == "POST":
        salary = request.form.get("salary")
        budget = MonthlyBudget(
            user_id=current_user.id,
            month_start_date=cycle_start,
            salary_credited=float(salary),
        )
        db.session.add(budget)
        db.session.commit()
        return redirect(url_for("index"))
    return render_template("set_budget.html", cycle_start=cycle_start)


@app.route("/add_transaction", methods=["POST"])
@login_required
def add_transaction():
    amount = float(request.form.get("amount"))
    description = request.form.get("description")

    new_transaction = Transaction(
        user_id=current_user.id,
        amount=amount,
        description=description,
        date=datetime.date.today(),
        time=datetime.datetime.now().time(),
    )
    db.session.add(new_transaction)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/update_profile", methods=["POST"])
@login_required
def update_profile():
    billing_cycle = request.form.get("billing_cycle")
    if billing_cycle:
        current_user.salary_credit_day = int(billing_cycle)
        db.session.commit()
        flash("Profile updated successfully")
    return redirect(url_for("index"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.context_processor
def inject_budgets():
    if current_user.is_authenticated:
        budgets = (
            MonthlyBudget.query.filter_by(user_id=current_user.id)
            .order_by(MonthlyBudget.month_start_date.desc())
            .all()
        )
        return dict(available_budgets=budgets)
    return dict(available_budgets=[])


@app.route("/download_statement")
@login_required
def download_statement():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    if year and month:
        # Find the budget that started in that specific month
        import sqlalchemy
        budget = MonthlyBudget.query.filter(
            MonthlyBudget.user_id == current_user.id,
            sqlalchemy.extract("year", MonthlyBudget.month_start_date) == year,
            sqlalchemy.extract("month", MonthlyBudget.month_start_date) == month,
        ).first()

        if not budget:
            flash(f"No biological data found for {datetime.date(year, month, 1).strftime('%B %Y')}.")
            return redirect(url_for("index"))
        
        cycle_start = budget.month_start_date
    else:
        cycle_start = get_current_cycle_start(current_user.salary_credit_day)
        budget = MonthlyBudget.query.filter_by(
            user_id=current_user.id, month_start_date=cycle_start
        ).first()

    if not budget:
        flash("No biological data found for current cycle.")
        return redirect(url_for("index"))

    # Determine the end of the selected cycle
    next_month_date = cycle_start + datetime.timedelta(days=32)
    next_cycle_start = get_current_cycle_start(
        current_user.salary_credit_day, today=next_month_date
    )

    transactions = (
        Transaction.query.filter(
            Transaction.user_id == current_user.id,
            Transaction.date >= cycle_start,
            Transaction.date < next_cycle_start,
        )
        .order_by(Transaction.date.asc())
        .all()
    )

    total_spent = sum(t.amount for t in transactions)
    remaining_balance = budget.salary_credited - total_spent

    # Create PDF
    pdf = FPDF()
    pdf.add_page()

    # Set background color
    pdf.set_fill_color(10, 11, 16)
    pdf.rect(0, 0, 210, 297, "F")

    # Header
    pdf.set_text_color(0, 210, 255)  # Cyan
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(
        0,
        20,
        "HAKOGANE - FINANCIAL STATEMENT",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="C",
    )

    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(138, 141, 164)
    pdf.cell(
        0,
        10,
        f"Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="C",
    )
    pdf.ln(10)

    # Summary Info
    pdf.set_text_color(240, 240, 245)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(
        0, 10, f"Operator: {current_user.username}", new_x=XPos.LMARGIN, new_y=YPos.NEXT
    )
    pdf.cell(
        0,
        10,
        f"Cycle Start: {cycle_start.strftime('%d %b %Y')}",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.ln(5)

    pdf.set_font("Helvetica", "", 12)
    # Simple table for stats
    pdf.cell(90, 10, "Metric", border=1)
    pdf.cell(90, 10, "Value (INR)", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.cell(90, 10, "Monthly Credit Allocation")
    pdf.cell(
        90,
        10,
        f"Rs. {budget.salary_credited:.2f}",
        border=1,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )

    pdf.set_text_color(255, 85, 85)
    pdf.cell(90, 10, "Total Consumption")
    pdf.cell(
        90, 10, f"Rs. {total_spent:.2f}", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT
    )

    pdf.set_text_color(0, 255, 170)
    pdf.cell(90, 10, "Available Reserve")
    pdf.cell(
        90,
        10,
        f"Rs. {remaining_balance:.2f}",
        border=1,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )

    pdf.ln(15)

    # Transaction Logs
    pdf.set_text_color(0, 210, 255)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "TRANSACTION PROTOCOLS", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    # Table Header
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(10, 11, 16)
    pdf.set_fill_color(0, 210, 255)
    pdf.cell(30, 10, "Date", border=1, fill=True)
    pdf.cell(30, 10, "Time", border=1, fill=True)
    pdf.cell(90, 10, "Description", border=1, fill=True)
    pdf.cell(
        40, 10, "Amount (INR)", border=1, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT
    )

    # Table Rows
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(240, 240, 245)
    for t in transactions:
        pdf.cell(30, 10, t.date.strftime("%Y-%m-%d"), border=1)
        pdf.cell(30, 10, t.time.strftime("%H:%M:%S"), border=1)
        pdf.cell(90, 10, t.description[:45], border=1)
        pdf.cell(
            40, 10, f"{t.amount:.2f}", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )

    pdf.ln(20)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(138, 141, 164)
    pdf.cell(
        0,
        10,
        "End of Hakogane Protocol Statement.",
        align="C",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )

    # Final output
    pdf_bytes = pdf.output()
    buffer = io.BytesIO(pdf_bytes)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Hakogane_Statement_{cycle_start.strftime('%b_%Y')}.pdf",
        mimetype="application/pdf",
    )


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
