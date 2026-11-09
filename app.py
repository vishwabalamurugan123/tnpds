import os
import io
import json
import sqlite3
import zipfile
import tempfile
from datetime import date, datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, g
from openpyxl import load_workbook
from werkzeug.utils import secure_filename

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "panruti_tnpds.db")
COMMODITY_FILE = os.path.join(APP_DIR, "commodity_master.json")

app = Flask(__name__)
app.secret_key = os.environ.get("TNPDS_SECRET", "local-tnpds-change-this-secret")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS shops(
      fps_code TEXT PRIMARY KEY, fps_name TEXT, godown_name TEXT, godown_code TEXT
    );
    CREATE TABLE IF NOT EXISTS commodities(
      commodity TEXT PRIMARY KEY
    );
    CREATE TABLE IF NOT EXISTS dispatches(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      dispatch_date TEXT NOT NULL, fps_code TEXT NOT NULL,
      commodity TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity >= 0),
      challan TEXT, remarks TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(fps_code) REFERENCES shops(fps_code)
    );
    CREATE TABLE IF NOT EXISTS allocations(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      allocation_month TEXT NOT NULL, fps_code TEXT NOT NULL,
      commodity TEXT NOT NULL, final_quantity REAL NOT NULL CHECK(final_quantity >= 0),
      UNIQUE(allocation_month,fps_code,commodity),
      FOREIGN KEY(fps_code) REFERENCES shops(fps_code)
    );
    """)
    # Restore the supplied master lists if the app is opened with a fresh/empty DB.
    # INSERT OR IGNORE keeps all existing user records and edits; nothing is deleted.
    shop_file = os.path.join(APP_DIR, "shop_master.json")
    if os.path.exists(shop_file):
        with open(shop_file, encoding="utf-8") as f:
            shops = json.load(f)
        conn.executemany(
            "INSERT OR IGNORE INTO shops(fps_code,fps_name,godown_name,godown_code) VALUES (?,?,?,?)",
            [(str(x.get("fps_code", "")).strip(), str(x.get("fps_name", "")).strip(),
              str(x.get("godown_name", "")).strip(), str(x.get("godown_code", "")).strip())
             for x in shops if str(x.get("fps_code", "")).strip()]
        )
    if os.path.exists(COMMODITY_FILE):
        with open(COMMODITY_FILE, encoding="utf-8") as f:
            commodities = json.load(f)
        conn.executemany("INSERT OR IGNORE INTO commodities(commodity) VALUES (?)",
                         [(str(c).strip(),) for c in commodities if str(c).strip()])
    conn.commit()
    conn.close()


def get_month():
    return request.args.get("month") or date.today().strftime("%Y-%m")


@app.route("/")
def dashboard():
    month = get_month()
    conn = db()
    shop_count = conn.execute("SELECT COUNT(*) n FROM shops").fetchone()["n"]
    item_count = conn.execute("SELECT COUNT(*) n FROM commodities").fetchone()["n"]
    dispatch_count = conn.execute(
        "SELECT COUNT(*) n FROM dispatches WHERE substr(dispatch_date,1,7)=?", (month,)
    ).fetchone()["n"]
    alloc_count = conn.execute(
        "SELECT COUNT(*) n FROM allocations WHERE allocation_month=?", (month,)
    ).fetchone()["n"]
    pending = conn.execute("""
      SELECT COUNT(*) n FROM allocations a
      LEFT JOIN (
        SELECT fps_code,commodity,SUM(quantity) sent
        FROM dispatches GROUP BY fps_code,commodity
      ) d ON d.fps_code=a.fps_code AND d.commodity=a.commodity
      WHERE a.allocation_month=? AND a.final_quantity-COALESCE(d.sent,0)>0
    """, (month,)).fetchone()["n"]
    return render_template("dashboard.html", month=month, shop_count=shop_count,
        item_count=item_count, dispatch_count=dispatch_count, alloc_count=alloc_count, pending=pending)


@app.route("/dispatch", methods=["GET", "POST"])
def dispatch():
    conn = db()
    if request.method == "POST":
        fps_code = request.form.get("fps_code", "").strip()
        dispatch_date = request.form.get("dispatch_date", "").strip()
        challan = request.form.get("challan", "").strip()
        remarks = request.form.get("remarks", "").strip()
        commodities = request.form.getlist("commodity[]")
        quantities = request.form.getlist("quantity[]")
        try:
            if not fps_code or not dispatch_date:
                raise ValueError("Choose a shop and dispatch date.")
            if not conn.execute("SELECT 1 FROM shops WHERE fps_code=?", (fps_code,)).fetchone():
                raise ValueError("Selected shop does not exist.")
            if not commodities or len(commodities) != len(quantities):
                raise ValueError("Add at least one commodity and quantity.")
            entries = []
            for commodity, raw_qty in zip(commodities, quantities):
                commodity = commodity.strip()
                if not commodity:
                    continue
                if not conn.execute("SELECT 1 FROM commodities WHERE commodity=?", (commodity,)).fetchone():
                    raise ValueError(f"Unknown commodity: {commodity}")
                qty = float(raw_qty)
                if qty < 0:
                    raise ValueError("Quantity cannot be negative.")
                entries.append((dispatch_date, fps_code, commodity, qty, challan, remarks))
            if not entries:
                raise ValueError("Enter at least one valid commodity and quantity.")
            # All commodity rows for this shop are committed together.
            conn.executemany("""INSERT INTO dispatches
                (dispatch_date,fps_code,commodity,quantity,challan,remarks)
                VALUES(?,?,?,?,?,?)""", entries)
            conn.commit()
            flash(f"Saved {len(entries)} commodity entries for FPS shop {fps_code}.", "success")
        except Exception as e:
            conn.rollback()
            flash("Nothing was saved. " + str(e), "error")
        return redirect(url_for("dispatch"))
    shops = conn.execute("SELECT * FROM shops ORDER BY fps_code").fetchall()
    commodities = conn.execute("SELECT commodity FROM commodities ORDER BY commodity").fetchall()
    recent = conn.execute("""SELECT d.*,s.fps_name FROM dispatches d JOIN shops s USING(fps_code)
      ORDER BY dispatch_date DESC,id DESC LIMIT 200""").fetchall()
    return render_template("dispatch.html", shops=shops, commodities=commodities,
                           recent=recent, today=date.today().isoformat())


@app.route("/dispatch/<int:dispatch_id>/delete", methods=["POST"])
def delete_dispatch(dispatch_id):
    conn = db()
    row = conn.execute("SELECT id FROM dispatches WHERE id=?", (dispatch_id,)).fetchone()
    if not row:
        flash("Dispatch entry was not found. It may already have been deleted.", "error")
    else:
        conn.execute("DELETE FROM dispatches WHERE id=?", (dispatch_id,))
        conn.commit()
        flash("Incorrect dispatch quantity entry deleted. Shop balance will recalculate automatically.", "success")
    return redirect(url_for("dispatch"))


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        month = request.form.get("month", "").strip()
        if not file or not file.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("Please choose a government .xlsx or .xlsm file.", "error")
            return redirect(url_for("upload"))
        if not month:
            flash("Select the allocation month.", "error")
            return redirect(url_for("upload"))
        try:
            wb = load_workbook(file, read_only=True, data_only=True)
            ws = wb[request.form.get("sheet") or wb.sheetnames[0]]
            header_row = None
            for r in range(1, min(ws.max_row, 40) + 1):
                vals = [str(ws.cell(r, c).value or "").strip() for c in range(1, ws.max_column + 1)]
                if "FPS Code" in vals:
                    header_row, headers = r, vals
                    break
            if not header_row:
                raise ValueError("Could not find a row containing 'FPS Code'.")
            code_col = headers.index("FPS Code") + 1
            known = {x["commodity"] for x in db().execute("SELECT commodity FROM commodities")}
            commodity_cols = [(c, h) for c, h in enumerate(headers, 1) if c > code_col and h in known]
            if not commodity_cols:
                raise ValueError("No recognized commodity columns were found.")
            conn = db()
            count = 0
            for row in range(header_row + 1, ws.max_row + 1):
                code = ws.cell(row, code_col).value
                if not code:
                    continue
                code = str(code).strip()
                if not conn.execute("SELECT 1 FROM shops WHERE fps_code=?", (code,)).fetchone():
                    continue
                for col, commodity in commodity_cols:
                    val = ws.cell(row, col).value
                    if val is None or str(val).strip() == "":
                        continue
                    qty = float(val)
                    if qty < 0:
                        raise ValueError("Allocation quantity cannot be negative.")
                    conn.execute("""INSERT INTO allocations(allocation_month,fps_code,commodity,final_quantity)
                      VALUES(?,?,?,?) ON CONFLICT(allocation_month,fps_code,commodity)
                      DO UPDATE SET final_quantity=excluded.final_quantity""", (month, code, commodity, qty))
                    count += 1
            conn.commit()
            wb.close()
            flash(f"Allocation imported for {count} shop/commodity cells for {month}. Verify against the signed government order.", "success")
        except Exception as e:
            db().rollback()
            flash("Import failed: " + str(e), "error")
        return redirect(url_for("balances", month=month))
    return render_template("upload.html", month=date.today().strftime("%Y-%m"))


@app.route("/balances")
def balances():
    month = get_month()
    rows = db().execute("""SELECT a.fps_code,s.fps_name,s.godown_code,s.godown_name,a.commodity,
      a.final_quantity,COALESCE(d.sent,0) advance_sent,
      MAX(a.final_quantity-COALESCE(d.sent,0),0) balance,
      MAX(COALESCE(d.sent,0)-a.final_quantity,0) over_dispatch
      FROM allocations a JOIN shops s USING(fps_code)
      LEFT JOIN (SELECT fps_code,commodity,SUM(quantity) sent FROM dispatches GROUP BY fps_code,commodity) d
      ON d.fps_code=a.fps_code AND d.commodity=a.commodity
      WHERE a.allocation_month=? ORDER BY s.godown_code,s.fps_code,a.commodity""", (month,)).fetchall()
    return render_template("balances.html", rows=rows, month=month)


@app.route("/daily-report")
def daily_report():
    # One row per shop; each registered commodity is a separate quantity column.
    report_date = request.args.get("dispatch_date") or date.today().isoformat()
    try:
        datetime.strptime(report_date, "%Y-%m-%d")
    except ValueError:
        report_date = date.today().isoformat()

    conn = db()
    commodities = [r["commodity"] for r in conn.execute(
        "SELECT commodity FROM commodities ORDER BY commodity").fetchall()]
    raw_rows = conn.execute("""SELECT s.fps_code, s.fps_name, s.godown_code,
        s.godown_name, d.commodity, SUM(d.quantity) AS quantity
        FROM dispatches d JOIN shops s ON s.fps_code=d.fps_code
        WHERE d.dispatch_date=?
        GROUP BY s.fps_code, s.fps_name, s.godown_code, s.godown_name, d.commodity
        ORDER BY s.godown_code, s.fps_code""", (report_date,)).fetchall()

    shop_map = {}
    for r in raw_rows:
        code = r["fps_code"]
        if code not in shop_map:
            shop_map[code] = {
                "fps_code": code, "fps_name": r["fps_name"],
                "godown": r["godown_code"] or r["godown_name"] or "-",
                "quantities": {c: 0 for c in commodities}, "total": 0
            }
        qty = r["quantity"] or 0
        shop_map[code]["quantities"][r["commodity"]] = qty
        shop_map[code]["total"] += qty

    shops = list(shop_map.values())
    totals = {c: sum(row["quantities"].get(c, 0) for row in shops) for c in commodities}
    grand_total = sum(totals.values())
    summary = {"shop_count": len(shops), "grand_total": grand_total}
    return render_template("daily_report.html", report_date=report_date,
        shops=shops, commodities=commodities, totals=totals, summary=summary,
        generated_at=datetime.now().strftime("%d-%m-%Y %I:%M %p"))

@app.route("/reports.csv")
def reports_csv():
    month = get_month()
    rows = db().execute("""SELECT a.fps_code,s.fps_name,s.godown_code,a.commodity,a.final_quantity,
      COALESCE(d.sent,0) advance_sent,MAX(a.final_quantity-COALESCE(d.sent,0),0) balance,
      MAX(COALESCE(d.sent,0)-a.final_quantity,0) over_dispatch
      FROM allocations a JOIN shops s USING(fps_code)
      LEFT JOIN (SELECT fps_code,commodity,SUM(quantity) sent FROM dispatches GROUP BY fps_code,commodity) d
      ON d.fps_code=a.fps_code AND d.commodity=a.commodity
      WHERE a.allocation_month=? ORDER BY s.godown_code,s.fps_code,a.commodity""", (month,)).fetchall()
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["FPS Code", "FPS Name", "Godown Code", "Commodity", "Final Allocation",
                     "Advance Sent", "Balance To Send", "Over Dispatch"])
    for r in rows:
        writer.writerow(list(r))
    mem = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return send_file(mem, mimetype="text/csv", as_attachment=True,
                     download_name=f"Panruti_FPS_Balance_{month}.csv")


@app.route("/shops", methods=["GET", "POST"])
def shops():
    conn = db()
    if request.method == "POST":
        code = request.form.get("fps_code", "").strip()
        name = request.form.get("fps_name", "").strip()
        if not code or not name:
            flash("FPS code and shop name are required.", "error")
        else:
            try:
                conn.execute("INSERT INTO shops(fps_code,fps_name,godown_name,godown_code) VALUES(?,?,?,?)",
                    (code, name, request.form.get("godown_name", "").strip(),
                     request.form.get("godown_code", "").strip()))
                conn.commit()
                flash("Shop added successfully.", "success")
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("That FPS code already exists.", "error")
        return redirect(url_for("shops"))
    rows = conn.execute("SELECT * FROM shops ORDER BY godown_code,fps_code").fetchall()
    return render_template("shops.html", rows=rows)


@app.route("/shops/<fps_code>/edit", methods=["POST"])
def edit_shop(fps_code):
    conn = db()
    name = request.form.get("fps_name", "").strip()
    if not name:
        flash("Shop name is required.", "error")
    else:
        conn.execute("UPDATE shops SET fps_name=?,godown_name=?,godown_code=? WHERE fps_code=?",
            (name, request.form.get("godown_name", "").strip(),
             request.form.get("godown_code", "").strip(), fps_code))
        conn.commit()
        flash("Shop updated.", "success")
    return redirect(url_for("shops"))


@app.route("/shops/<fps_code>/delete", methods=["POST"])
def delete_shop(fps_code):
    conn = db()
    used = conn.execute("SELECT (SELECT COUNT(*) FROM dispatches WHERE fps_code=?) + "
                        "(SELECT COUNT(*) FROM allocations WHERE fps_code=?)", (fps_code, fps_code)).fetchone()[0]
    if used:
        flash("Cannot delete this shop because dispatch or allocation records are linked to it. Edit it instead.", "error")
    else:
        conn.execute("DELETE FROM shops WHERE fps_code=?", (fps_code,))
        conn.commit()
        flash("Shop deleted.", "success")
    return redirect(url_for("shops"))


@app.route("/items", methods=["GET", "POST"])
def items():
    conn = db()
    if request.method == "POST":
        name = request.form.get("commodity", "").strip()
        if not name:
            flash("Item name is required.", "error")
        else:
            try:
                conn.execute("INSERT INTO commodities(commodity) VALUES (?)", (name,))
                conn.commit()
                flash("Item added successfully.", "success")
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("This item already exists.", "error")
        return redirect(url_for("items"))
    rows = conn.execute("""SELECT c.commodity,
        (SELECT COUNT(*) FROM allocations a WHERE a.commodity=c.commodity) +
        (SELECT COUNT(*) FROM dispatches d WHERE d.commodity=c.commodity) AS used_count
        FROM commodities c ORDER BY c.commodity""").fetchall()
    return render_template("items.html", rows=rows)


@app.route("/items/<path:old_name>/edit", methods=["POST"])
def edit_item(old_name):
    new_name = request.form.get("commodity", "").strip()
    if not new_name:
        flash("Item name is required.", "error")
    else:
        try:
            conn = db()
            conn.execute("UPDATE commodities SET commodity=? WHERE commodity=?", (new_name, old_name))
            conn.execute("UPDATE allocations SET commodity=? WHERE commodity=?", (new_name, old_name))
            conn.execute("UPDATE dispatches SET commodity=? WHERE commodity=?", (new_name, old_name))
            conn.commit()
            flash("Item updated.", "success")
        except sqlite3.IntegrityError:
            db().rollback()
            flash("Could not rename item. The new item name may already exist.", "error")
    return redirect(url_for("items"))


@app.route("/items/<path:name>/delete", methods=["POST"])
def delete_item(name):
    conn = db()
    used = conn.execute("SELECT (SELECT COUNT(*) FROM allocations WHERE commodity=?) + "
                        "(SELECT COUNT(*) FROM dispatches WHERE commodity=?)", (name, name)).fetchone()[0]
    if used:
        flash("Cannot delete an item that has allocation or dispatch history. Rename it if needed.", "error")
    else:
        conn.execute("DELETE FROM commodities WHERE commodity=?", (name,))
        conn.commit()
        flash("Item deleted.", "success")
    return redirect(url_for("items"))


@app.route("/backup")
def backup():
    # SQLite's backup API makes a consistent copy even while the app is running.
    temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp.close()
    try:
        source = sqlite3.connect(DB_PATH)
        target = sqlite3.connect(temp.name)
        source.backup(target)
        target.close()
        source.close()
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(temp.name, "panruti_tnpds.db")
            metadata = {
                "application": "TNPDS Panruti Taluk",
                "backup_created": datetime.now().isoformat(timespec="seconds"),
                "database_file": "panruti_tnpds.db"
            }
            zf.writestr("backup_info.json", json.dumps(metadata, indent=2))
        memory.seek(0)
        filename = f"TNPDS_Panruti_Backup_{datetime.now():%Y%m%d_%H%M%S}.zip"
        return send_file(memory, mimetype="application/zip", as_attachment=True, download_name=filename)
    finally:
        try:
            os.remove(temp.name)
        except OSError:
            pass


@app.route("/restore", methods=["GET", "POST"])
def restore():
    if request.method == "POST":
        uploaded = request.files.get("backup_file")
        if not uploaded or not uploaded.filename:
            flash("Choose a backup ZIP file.", "error")
            return redirect(url_for("restore"))
        if not uploaded.filename.lower().endswith(".zip"):
            flash("Only a TNPDS backup .zip file is accepted.", "error")
            return redirect(url_for("restore"))
        temp_path = None
        try:
            with zipfile.ZipFile(uploaded) as zf:
                names = zf.namelist()
                if "panruti_tnpds.db" not in names:
                    raise ValueError("This ZIP does not contain panruti_tnpds.db.")
                data = zf.read("panruti_tnpds.db")
            fd, temp_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            with open(temp_path, "wb") as f:
                f.write(data)
            check = sqlite3.connect(temp_path)
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            required = {"shops", "commodities", "dispatches", "allocations"}
            actual = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if integrity != "ok" or not required.issubset(actual):
                raise ValueError("Backup database is invalid or does not match this application.")
            check.close()
            # Close current request's connection before replacing the database file.
            conn = g.pop("db", None)
            if conn:
                conn.close()
            os.replace(temp_path, DB_PATH)
            temp_path = None
            flash("Backup restored successfully. The application data has been replaced by the selected backup.", "success")
        except Exception as e:
            flash("Restore failed: " + str(e), "error")
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        return redirect(url_for("dashboard"))
    return render_template("restore.html")


if __name__ == "__main__":
    init_db()
    print("TNPDS Panruti Taluk running at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
