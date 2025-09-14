"""
Time Tracker App (improved v11)

Fixes in this iteration:
- Fixed: "Edit selected" now works reliably even if row was selected via keyboard or mouse.
- Added: Robust row selection handler for tksheet (handles 'row' and 'rows' events).
- Can edit task name and W flag during entry editing.
- Visual improvements.

Dependencies:
- tksheet (pip install tksheet)
- tkcalendar (optional)
- pandas (optional, for export)

Run: python time_tracker_sqlite.py
"""

import os
import sqlite3
from datetime import datetime, date
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import ttkbootstrap as tb

    USE_BOOTSTRAP = True
except Exception:
    USE_BOOTSTRAP = False

# Prefer tkcalendar but fallback to a simple date field
try:
    from tkcalendar import DateEntry as TKDateEntry

    TKCAL_AVAILABLE = True
except Exception:
    TKCAL_AVAILABLE = False

try:
    import pandas as pd
except Exception:
    pd = None

try:
    import tksheet
except Exception:
    raise ImportError("Установите библиотеку tksheet: pip install tksheet")

DB_FILE = "time_tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category TEXT DEFAULT 'General',
    w INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT,
    duration_h REAL DEFAULT 0,
    date_key TEXT NOT NULL,
    active INTEGER DEFAULT 0,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
"""


# --- Storage ---
class Storage:
    def __init__(self, path=DB_FILE):
        init_needed = not os.path.exists(path)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if init_needed:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        else:
            self._migrate()

    def _migrate(self):
        cur = self.conn.execute("PRAGMA table_info(entries)")
        cols = [r[1] for r in cur.fetchall()]
        if 'active' not in cols:
            self.conn.execute("ALTER TABLE entries ADD COLUMN active INTEGER DEFAULT 0")
            self.conn.commit()

    def add_task(self, name, category='General', w=0):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO tasks (name, category, w) VALUES (?,?,?)",
                        (name.strip(), category, int(bool(w))))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            cur.execute("SELECT id FROM tasks WHERE name=?", (name.strip(),))
            r = cur.fetchone()
            return r['id'] if r else None

    def list_tasks(self):
        cur = self.conn.execute("SELECT * FROM tasks ORDER BY w DESC, name")
        return [dict(r) for r in cur.fetchall()]

    def start_entry(self, task_id, date_key=None):
        start_ts = datetime.now().isoformat()
        date_key = date_key or start_ts[:10]
        self.conn.execute("UPDATE entries SET active=0 WHERE active=1")
        cur = self.conn.cursor()
        cur.execute("INSERT INTO entries (task_id, start_ts, date_key, active) VALUES (?,?,?,1)",
                    (task_id, start_ts, date_key))
        self.conn.commit()
        return cur.lastrowid

    def stop_entry(self, entry_id=None):
        end_ts = datetime.now().isoformat()
        if entry_id:
            cur = self.conn.execute("SELECT start_ts FROM entries WHERE id=?", (entry_id,))
        else:
            cur = self.conn.execute("SELECT id, start_ts FROM entries WHERE active=1")
        r = cur.fetchone()
        if not r:
            return False
        entry_id = entry_id or r['id']
        start_ts = r['start_ts']
        start = datetime.fromisoformat(start_ts)
        end = datetime.fromisoformat(end_ts)
        dur = (end - start).total_seconds() / 3600.0
        self.conn.execute("UPDATE entries SET end_ts=?, duration_h=?, active=0 WHERE id=?",
                          (end_ts, round(dur, 2), entry_id))
        self.conn.commit()
        return True

    def pause_active(self):
        cur = self.conn.execute("SELECT id FROM entries WHERE active=1")
        r = cur.fetchone()
        if not r:
            return False
        return self.stop_entry(r['id'])

    def list_entries_for_date(self, date_key):
        cur = self.conn.execute(
            "SELECT e.*, t.name as task_name, t.w as w FROM entries e JOIN tasks t ON e.task_id=t.id "
            "WHERE e.date_key=? ORDER BY t.w DESC, e.start_ts",
            (date_key,)
        )
        return [dict(r) for r in cur.fetchall()]

    def add_empty_entry(self, task_id, date_key):
        self.conn.execute(
            "INSERT INTO entries (task_id, start_ts, end_ts, duration_h, date_key, active) VALUES (?,?,?,?,?,?)",
            (task_id, f"{date_key}T00:00:00", f"{date_key}T00:00:00", 0, date_key, 0)
        )
        self.conn.commit()

    def update_entry(self, entry_id, start_ts, end_ts):
        start = datetime.fromisoformat(start_ts)
        end = datetime.fromisoformat(end_ts)
        dur = round((end - start).total_seconds() / 3600.0, 2)
        self.conn.execute(
            "UPDATE entries SET start_ts=?, end_ts=?, duration_h=?, date_key=?, task_id=(SELECT task_id FROM entries WHERE id=?) WHERE id=?",
            (start_ts, end_ts, dur, start.date().isoformat(), entry_id, entry_id)
        )
        self.conn.commit()

    def delete_entry(self, entry_id):
        self.conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self.conn.commit()

    def export_date_to_df(self, date_key):
        rows = self.list_entries_for_date(date_key)
        if not rows or pd is None:
            return None
        data = []
        for r in rows:
            data.append({
                'Task': r['task_name'],
                'Start': r['start_ts'],
                'End': r['end_ts'] or '',
                'Duration_h': r['duration_h'],
                'W': r['w']
            })
        df = pd.DataFrame(data)
        summary = df.groupby(['Task', 'W'], as_index=False)['Duration_h'].sum()
        total = pd.DataFrame([{'Task': 'Total', 'W': '', 'Duration_h': summary['Duration_h'].sum()}])
        return pd.concat([summary, total], ignore_index=True)


# --- Fallback Date Entry ---
class SimpleDateEntry(ttk.Entry):
    """Fallback date entry that displays date in dd.mm.yy"""

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.set_date(date.today())

    def get_date(self):
        txt = self.get().strip()
        for fmt in ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(txt, fmt).date()
            except Exception:
                continue
        return date.today()

    def set_date(self, d):
        if isinstance(d, (datetime, date)):
            s = d.strftime("%d.%m.%y")
        else:
            s = str(d)
        self.delete(0, tk.END)
        self.insert(0, s)


# --- Main UI Class ---
class TimeTrackerUI:
    def __init__(self, root):
        self.root = root
        self.storage = Storage()
        self.current_row = None
        self.row_map = {}
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        top = ttk.Frame(self.root)
        top.pack(fill=tk.X, pady=5)

        ttk.Label(top, text="Дата:").pack(side=tk.LEFT)
        if TKCAL_AVAILABLE:
            try:
                self.date_entry = TKDateEntry(top, date_pattern='dd.mm.yy', width=12)
            except Exception:
                self.date_entry = TKDateEntry(top, date_pattern='dd.mm.yyyy', width=12)
        else:
            self.date_entry = SimpleDateEntry(top, width=12)

        try:
            self.date_entry.set_date(date.today())
        except Exception:
            try:
                self.date_entry.delete(0, tk.END)
                self.date_entry.insert(0, date.today().strftime('%d.%m.%y'))
            except Exception:
                pass

        self.date_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Обновить", command=self._refresh).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Отчёт", command=self._show_report).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Экспорт", command=self._export_excel).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Добавить задачу", command=self._add_task_dialog).pack(side=tk.RIGHT, padx=5)

        # Sheet
        self.sheet = tksheet.Sheet(self.root,
                                   headers=["Task", "W", "Start", "End", "Duration", "Actions"],
                                   height=400, width=900)
        self.sheet.enable_bindings((
            "single_select", "row_select", "column_select", "arrowkeys"
        ))
        self.sheet.extra_bindings([
            ("cell_select", self._on_cell_click),
            ("row_select", self._on_row_select)
        ])
        self.sheet.pack(fill=tk.BOTH, expand=True)

        # Toolbar
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill=tk.X, pady=6)
        ttk.Button(toolbar, text="START (выделено)", command=self._toolbar_start).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="STOP (выделено)", command=self._toolbar_stop).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="PAUSE", command=self._pause).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Edit selected", command=self._edit_selected).pack(side=tk.LEFT, padx=8)
        ttk.Button(toolbar, text="Delete selected", command=self._delete_selected).pack(side=tk.LEFT, padx=8)

    def _get_date_key(self):
        try:
            if TKCAL_AVAILABLE:
                d = self.date_entry.get_date()
                if isinstance(d, date):
                    return d.isoformat()
            else:
                return self.date_entry.get_date().isoformat()
        except Exception:
            txt = getattr(self.date_entry, 'get', lambda: '')()
            for fmt in ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d"):
                try:
