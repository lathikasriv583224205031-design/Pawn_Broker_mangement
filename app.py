from flask import Flask, render_template, request, redirect, session, send_file, flash
import sqlite3
from datetime import datetime, timedelta
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
import sys
import importlib
import requests
import base64
import tempfile
import shutil
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IS_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

# On Vercel serverless environment, local filesystem is read-only except /tmp
if IS_VERCEL:
    DB_PATH = os.path.join(tempfile.gettempdir(), "pawn.db")
else:
    DB_PATH = os.path.join(BASE_DIR, "pawn.db")

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)
app.secret_key = os.environ.get("SECRET_KEY", "secret123")

def init_db():
    bundled_db = os.path.join(BASE_DIR, "pawn.db")
    if IS_VERCEL and os.path.exists(bundled_db) and not os.path.exists(DB_PATH):
        try:
            shutil.copy2(bundled_db, DB_PATH)
        except Exception as e:
            print("Notice: could not copy bundled DB:", e)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        password TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS loans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        item TEXT,
        gold_weight REAL,
        amount REAL,
        interest_rate REAL,
        months INTEGER,
        extra_amount REAL,
        total_amount REAL,
        due_date TEXT,
        status TEXT DEFAULT 'Active',
        manual_extra REAL DEFAULT 0,
        phone_number TEXT DEFAULT '',
        address TEXT DEFAULT '',
        last_msg_date TEXT DEFAULT ''
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sms_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_id INTEGER,
        customer_name TEXT,
        phone_number TEXT,
        message TEXT,
        language TEXT,
        sent_at TEXT,
        status TEXT,
        error_details TEXT
    )
    """)
    # Ensure default admin user exists
    cursor.execute("SELECT id FROM users WHERE username='velu'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password) VALUES ('velu', '86088')")

    conn.commit()
    conn.close()

# Initialize DB
try:
    init_db()
except Exception as e:
    print("Database init notice:", e)

def get_db():
    if not os.path.exists(DB_PATH):
        init_db()
    return sqlite3.connect(DB_PATH)

def log_sms(message):
    print("SMS Log:", message)
    try:
        log_path = os.path.join(tempfile.gettempdir() if IS_VERCEL else BASE_DIR, "sms_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception:
        pass

from requests.auth import HTTPBasicAuth

def get_twilio_credentials():
    sid = os.environ.get('TWILIO_ACCOUNT_SID')
    token = os.environ.get('TWILIO_AUTH_TOKEN')
    phone = os.environ.get('TWILIO_PHONE_NUMBER')

    try:
        if 'twilio_config' in sys.modules:
            import twilio_config
            importlib.reload(twilio_config)
        else:
            import twilio_config
        sid = getattr(twilio_config, 'TWILIO_ACCOUNT_SID', sid)
        token = getattr(twilio_config, 'TWILIO_AUTH_TOKEN', token)
        phone = getattr(twilio_config, 'TWILIO_PHONE_NUMBER', phone)
    except Exception:
        pass

    return sid or 'your_account_sid', token or 'your_auth_token', phone or 'your_twilio_phone_number'

def send_sms(to_phone, message):
    if not to_phone:
        return False, "No phone number provided"

    account_sid, auth_token, from_phone = get_twilio_credentials()

    # Validate Twilio credentials before calling API
    if (auth_token in ['your_auth_token', 'YOUR_VALID_AUTH_TOKEN', ''] or 
        account_sid in ['your_account_sid', ''] or 
        from_phone in ['your_twilio_phone_number', '']):
        err = "Twilio credentials not configured. Please update TWILIO_AUTH_TOKEN in twilio_config.py."
        log_sms(f"Config Error: {err}")
        return False, err

    try:
        # Sanitize phone number (strip whitespace, hyphens)
        clean_phone = str(to_phone).strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if clean_phone.startswith("0") and len(clean_phone) == 11:
            clean_phone = clean_phone[1:]
        if not clean_phone.startswith("+"):
            clean_phone = "+91" + clean_phone
            
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        data = {
            "From": from_phone,
            "To": clean_phone,
            "Body": message
        }
        auth = HTTPBasicAuth(account_sid, auth_token)
        response = requests.post(url, data=data, auth=auth)
        
        if response.status_code in [200, 201]:
            return True, "SMS sent successfully"
        else:
            try:
                error_body = response.json()
                error_msg = error_body.get('message', response.text)
            except Exception:
                error_msg = response.text

            log_sms(f"Twilio Error: {response.text}")
            return False, error_msg
            
    except Exception as e:
        log_sms(f"SMS Error: {e}")
        return False, str(e)

def calculate(amount, rate, months, manual_extra=0):
    extra = (amount * rate * months) / 100
    total = amount + extra + manual_extra
    due = datetime.now() + timedelta(days=30*months)
    return extra, total, due.strftime("%Y-%m-%d")

def safe_float(val, default=0.0):
    if not val or str(val).strip() == "" or str(val).strip() == "None":
        return default
    try:
        return float(str(val).replace('%', '').strip())
    except ValueError:
        return default

def safe_int(val, default=0):
    if not val or str(val).strip() == "" or str(val).strip() == "None":
        return default
    try:
        return int(val)
    except ValueError:
        return default

@app.context_processor
def inject_lang():
    return {'current_lang': session.get('lang', 'en')}

@app.route("/set_lang/<lang>")
def set_lang(lang):
    if lang in ['en', 'ta']:
        session['lang'] = lang
    referer = request.headers.get("Referer")
    if referer:
        return redirect(referer)
    return redirect("/dashboard")

@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        user = request.form["username"]
        pwd = request.form["password"]

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username=? AND password=?", (user,pwd))
        data = cursor.fetchone()
        conn.close()

        if data:
            session["user"] = user
            return redirect("/dashboard")

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    month_filter = request.args.get("month_filter")

    conn = get_db()
    cursor = conn.cursor()

    if month_filter:
        cursor.execute("SELECT * FROM loans WHERE strftime('%Y-%m', due_date) = ?", (month_filter,))
    else:
        cursor.execute("SELECT * FROM loans")
    data = cursor.fetchall()

    # Calculate dynamic interest for late payments
    processed_data = []
    for row in data:
        row_list = list(row)
        due_date_str = row_list[8]
        if due_date_str and row_list[10] != 'Returned':
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
            if datetime.now() > due_date:
                late_days = (datetime.now() - due_date).days
                late_months = (late_days // 30) + 1  # charge for full month if late
                extra_late_interest = (row_list[3] * row_list[4] * late_months) / 100
                row_list[6] += extra_late_interest  # update extra_amount
                row_list[7] += extra_late_interest  # update total_amount
        processed_data.append(row_list)

    # Month-wise summary
    cursor.execute("""
        SELECT strftime('%Y-%m', due_date) as month, 
               COUNT(id), 
               SUM(gold_weight), 
               SUM(amount), 
               SUM(extra_amount),
               SUM(CASE WHEN status='Returned' THEN 1 ELSE 0 END)
        FROM loans 
        GROUP BY month 
        ORDER BY month
    """)
    month_summary = cursor.fetchall()

    # Fetch recent SMS sent history (last 8 records)
    cursor.execute("SELECT * FROM sms_history ORDER BY id DESC LIMIT 8")
    recent_sms = cursor.fetchall()

    # Chart generation (in-memory for serverless/read-only compatibility)
    months_labels = [row[0] if row[0] else 'Unknown' for row in month_summary]
    monthly_amounts = [row[3] if row[3] else 0 for row in month_summary]

    chart_url = ""
    try:
        plt.figure(figsize=(6, 4))
        if months_labels:
            plt.bar(months_labels, monthly_amounts, color='#3498db')
            plt.xlabel('Month')
            plt.ylabel('Total Amount')
            plt.title('Month-wise Loan Summary')
            plt.xticks(rotation=45)
            plt.tight_layout()
        img_buf = BytesIO()
        plt.savefig(img_buf, format='png', bbox_inches='tight')
        plt.close()
        img_buf.seek(0)
        chart_url = "data:image/png;base64," + base64.b64encode(img_buf.getvalue()).decode('utf-8')

        # If not running on Vercel, save to static for local caching
        if not IS_VERCEL:
            static_dir = os.path.join(BASE_DIR, "static")
            if not os.path.exists(static_dir):
                os.makedirs(static_dir, exist_ok=True)
            with open(os.path.join(static_dir, "chart.png"), "wb") as f:
                f.write(img_buf.getvalue())
    except Exception as e:
        print("Chart generation error:", e)

    conn.close()
    return render_template("dashboard.html", data=processed_data, month_summary=month_summary, month_filter=month_filter, recent_sms=recent_sms, chart_url=chart_url)

@app.route("/add", methods=["GET","POST"])
def add():
    if request.method == "POST":
        name = request.form.get("name", "")
        item = request.form.get("item", "")
        gold = safe_float(request.form.get("gold"))
        amount = safe_float(request.form.get("amount"))
        rate = safe_float(request.form.get("rate"))
        months = safe_int(request.form.get("months"))
        manual_extra = safe_float(request.form.get("manual_extra"))
        status = request.form.get("status", "Active")
        phone = request.form.get("phone", "")
        address = request.form.get("address", "")

        extra, total, due = calculate(amount, rate, months, manual_extra)
        
        # Override if manually supplied
        if request.form.get("interest_amount"):
            extra = safe_float(request.form.get("interest_amount"))
        if request.form.get("total_amount"):
            total = safe_float(request.form.get("total_amount"))
        if request.form.get("due_date"):
            due = request.form.get("due_date")

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO loans (customer_name,item,gold_weight,amount,interest_rate,months,extra_amount,total_amount,due_date,status,manual_extra,phone_number,address)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(name,item,gold,amount,rate,months,extra,total,due,status,manual_extra,phone,address))

        conn.commit()
        conn.close()
        return redirect("/dashboard")

    return render_template("add.html")

@app.route("/search", methods=["POST"])
def search():
    name = request.form.get("search", "")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM loans WHERE customer_name LIKE ? OR status LIKE ?", ('%'+name+'%', '%'+name+'%'))
    data = cursor.fetchall()

    # Calculate dynamic interest for late payments
    processed_data = []
    for row in data:
        row_list = list(row)
        due_date_str = row_list[8]
        if due_date_str and row_list[10] != 'Returned':
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
            if datetime.now() > due_date:
                late_days = (datetime.now() - due_date).days
                late_months = (late_days // 30) + 1  # charge for full month if late
                extra_late_interest = (row_list[3] * row_list[4] * late_months) / 100
                row_list[6] += extra_late_interest  # update extra_amount
                row_list[7] += extra_late_interest  # update total_amount
        processed_data.append(row_list)

    cursor.execute("""
        SELECT strftime('%Y-%m', due_date) as month, 
               COUNT(id), 
               SUM(gold_weight), 
               SUM(amount), 
               SUM(extra_amount),
               SUM(CASE WHEN status='Returned' THEN 1 ELSE 0 END)
        FROM loans 
        GROUP BY month 
        ORDER BY month
    """)
    month_summary = cursor.fetchall()

    cursor.execute("SELECT * FROM sms_history ORDER BY id DESC LIMIT 8")
    recent_sms = cursor.fetchall()

    conn.close()

    return render_template("dashboard.html", data=processed_data, month_summary=month_summary, recent_sms=recent_sms)

@app.route("/delete/<int:id>")
def delete(id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM loans WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/dashboard")

@app.route("/edit/<int:id>", methods=["GET","POST"])
def edit(id):
    conn = get_db()
    cursor = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name", "")
        item = request.form.get("item", "")
        gold = safe_float(request.form.get("gold"))
        amount = safe_float(request.form.get("amount"))
        rate = safe_float(request.form.get("rate"))
        months = safe_int(request.form.get("months"))
        manual_extra = safe_float(request.form.get("manual_extra"))
        status = request.form.get("status", "Active")
        phone = request.form.get("phone", "")
        address = request.form.get("address", "")

        extra, total, due = calculate(amount, rate, months, manual_extra)
        
        # Override if manually supplied
        if request.form.get("interest_amount"):
            extra = safe_float(request.form.get("interest_amount"))
        if request.form.get("total_amount"):
            total = safe_float(request.form.get("total_amount"))
        if request.form.get("due_date"):
            due = request.form.get("due_date")

        # ✅ CORRECT UPDATE QUERY
        cursor.execute("""
        UPDATE loans 
        SET customer_name=?, item=?, gold_weight=?, amount=?, 
            interest_rate=?, months=?, extra_amount=?, 
            total_amount=?, due_date=?, status=?, manual_extra=?,
            phone_number=?, address=?
        WHERE id=?
        """, (name, item, gold, amount, rate, months, extra, total, due, status, manual_extra, phone, address, id))

        conn.commit()
        conn.close()
        return redirect("/dashboard")

    # GET request
    cursor.execute("SELECT * FROM loans WHERE id=?", (id,))
    data = cursor.fetchone()
    conn.close()

    return render_template("edit.html", row=data)    

@app.route("/customer_pdf/<int:id>")
def customer_pdf(id):
    if "user" not in session:
        return redirect("/")

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM loans WHERE id=?", (id,))
    data = cursor.fetchone()
    conn.close()

    if not data:
        return redirect("/dashboard")

    # Create a PDF in memory
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Draw title
    p.setFont("Helvetica-Bold", 18)
    p.drawCentredString(width / 2.0, height - 50, "Pawn Broker - Customer Receipt")

    # Draw line
    p.line(50, height - 60, width - 50, height - 60)

    # Draw customer details
    p.setFont("Helvetica", 12)
    y = height - 100
    line_height = 25

    details = [
        ("Customer Name:", data["customer_name"]),
        ("Phone Number:", data["phone_number"] if data["phone_number"] else "N/A"),
        ("Address:", data["address"] if data["address"] else "N/A"),
        ("Item Description:", data["item"]),
        ("Gold Weight (g):", f"{data['gold_weight']} g"),
        ("Loan Amount (Rs.):", f"Rs. {data['amount']}"),
        ("Interest Rate (%):", f"{data['interest_rate']} %"),
        ("Interest Expected (Rs.):", f"Rs. {data['extra_amount']}"),
        ("Total Amount (Rs.):", f"Rs. {data['total_amount']}"),
        ("Due Date:", data["due_date"]),
        ("Status:", data["status"])
    ]

    for label, value in details:
        p.setFont("Helvetica-Bold", 12)
        p.drawString(100, y, label)
        p.setFont("Helvetica", 12)
        p.drawString(300, y, str(value))
        y -= line_height

    # Footer
    p.line(50, y - 20, width - 50, y - 20)
    p.setFont("Helvetica-Oblique", 10)
    p.drawCentredString(width / 2.0, y - 40, "Thank you for your business. Please bring this receipt when returning.")

    p.showPage()
    p.save()

    buffer.seek(0)
    filename = f"Customer_Receipt_{data['id']}.pdf"
    if data['customer_name']:
        safe_name = "".join(c for c in data['customer_name'] if c.isalnum() or c in (' ', '_')).replace(' ', '_')
        filename = f"Receipt_{safe_name}.pdf"
        
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')

@app.route("/logout")
def logout():
    session.pop("user",None)
    return redirect("/")

@app.route("/send_sms/<int:id>")
def send_reminder_sms(id):
    lang = request.args.get('lang', session.get('lang', 'en'))
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT customer_name, item, total_amount, due_date, phone_number FROM loans WHERE id=?", (id,))
    data = cursor.fetchone()

    if data and data[4]:
        name, item, total, due, phone = data
        if lang == 'ta':
            msg = f"வணக்கம் {name}, உங்கள் {item} நகை கடன் (கெடு: {due}) நிலுவையில் உள்ளது. நிலுவை: ரூ.{total}. தயவுசெய்து செலுத்தவும். - Periya Karuppar Banker"
            lang_label = "Tamil"
        else:
            msg = f"Hello {name}, your gold loan for {item} is due on {due}. Total pending: Rs.{total}. Please clear your dues. - Periya Karuppar Banker"
            lang_label = "English"
        
        # Ensure phone number format
        if not phone.startswith("+"):
            phone = "+91" + phone  # Defaulting to India
            
        success, err_msg = send_sms(phone, msg)
        now_str = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        status = "Success" if success else "Failed"
        error_details = "" if success else err_msg

        # Store in sms_history
        cursor.execute("""
            INSERT INTO sms_history (loan_id, customer_name, phone_number, message, language, sent_at, status, error_details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id, name, phone, msg, lang_label, now_str, status, error_details))

        if success:
            cursor.execute("UPDATE loans SET last_msg_date=? WHERE id=?", (now_str, id))
            conn.commit()
            flash("SMS sent successfully!" if session.get('lang') != 'ta' else "SMS வெற்றிகரமாக அனுப்பப்பட்டது!", "success")
        else:
            conn.commit()
            flash(f"Failed to send SMS: {err_msg}", "error")

    conn.close()
    return redirect("/dashboard")

@app.route("/mass_warn")
def mass_warn():
    lang = request.args.get('lang', session.get('lang', 'en'))
    conn = get_db()
    cursor = conn.cursor()
    
    now_str = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    cursor.execute("SELECT id, customer_name, item, total_amount, due_date, phone_number FROM loans WHERE status='Active' AND phone_number != ''")
    active_loans = cursor.fetchall()
    
    sent_count = 0
    fail_count = 0
    last_err_msg = ""
    lang_label = "Tamil" if lang == 'ta' else "English"

    for row in active_loans:
        loan_id, name, item, total, due, phone = row
        if lang == 'ta':
            msg = f"வணக்கம் {name}, உங்கள் {item} நகை கடன் (கெடு: {due}) நிலுவையில் உள்ளது. நிலுவை: ரூ.{total}. தயவுசெய்து செலுத்தவும். - Periya Karuppar Banker"
        else:
            msg = f"Hello {name}, gentle reminder: your gold loan for {item} is currently active. Total pending: Rs.{total}. Due date: {due}. - Periya Karuppar Banker"
        
        if not phone.startswith("+"):
            phone = "+91" + phone
            
        success, err_msg = send_sms(phone, msg)
        status = "Success" if success else "Failed"
        error_details = "" if success else err_msg

        # Store in sms_history
        cursor.execute("""
            INSERT INTO sms_history (loan_id, customer_name, phone_number, message, language, sent_at, status, error_details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (loan_id, name, phone, msg, lang_label, now_str, status, error_details))

        if success:
            cursor.execute("UPDATE loans SET last_msg_date=? WHERE id=?", (now_str, loan_id))
            sent_count += 1
        else:
            fail_count += 1
            last_err_msg = err_msg
            
    conn.commit()
    conn.close()
    
    if sent_count > 0:
        flash(f"Successfully sent mass SMS to {sent_count} customers.", "success")
    if last_err_msg:
        flash(f"Failed for {fail_count} customers: {last_err_msg}", "error")
    elif sent_count == 0 and not last_err_msg:
        flash("No active customers to send SMS.", "info")
    
    return redirect("/dashboard")

@app.route("/sms_history")
def sms_history():
    if "user" not in session:
        return redirect("/")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sms_history ORDER BY id DESC")
    history_data = cursor.fetchall()
    conn.close()
    
    return render_template("sms_history.html", history=history_data)

@app.route("/sms_history/clear", methods=["POST"])
def clear_sms_history():
    if "user" not in session:
        return redirect("/")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sms_history")
    conn.commit()
    conn.close()
    flash("SMS history cleared successfully.", "info")
    return redirect("/sms_history")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)