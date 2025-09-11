"""
Time Tracker App (improved)
Features implemented:
- SQLite storage (tasks, entries, settings) for reliability and queries
- Modernized UI with ttkbootstrap if available (falls back to ttk)
- Date selector on main window to view/track different dates
- START/STOP/Pause buttons (real buttons, auto-stop other active tasks)
- Task priority (W), categories, and quick-add
- Daily/weekly/monthly reports and Excel export
- Automatic backup of DB
- Autosave and robust error handling

Dependencies:
- tkcalendar (pip install tkcalendar)
- pandas (pip install pandas)
- openpyxl (pip install openpyxl) for Excel export
- ttkbootstrap (optional, pip install ttkbootstrap)

Run: python time_tracker_sqlite.py
"""

import os
import sqlite3
import threading
from datetime import datetime, date, timedelta
import json
import shutil
import tempfile

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

# Optional modern ttk theme
try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *
    USE_BOOTSTRAP = True
except Exception:
    USE_BOOTSTRAP = False

# Third-party
try:
    from tkcalendar import DateEntry
except Exception:
    raise SystemExit("Please install tkcalendar: pip install tkcalendar")

try:
    import pandas as pd
except Exception:
    pd = None

DB_FILE = "time_tracker.db"
BACKUP_DIR = "backups"
AUTOBACKUP_DAYS = 3

SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category TEXT DEFAULT 'General',
    w INTEGER DEFAULT 0,
    color TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT,
    duration_h REAL DEFAULT 0,
    note TEXT DEFAULT '',
    date_key TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# ---------- Storage layer ----------
class Storage:
    def __init__(self, path=DB_FILE):
        self.path = path
        self._ensure_db()

    def _ensure_db(self):
        init_needed = not os.path.exists(self.path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if init_needed:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def backup(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(BACKUP_DIR, f"time_tracker_{ts}.db")
        self.conn.close()
        shutil.copyfile(self.path, dest)
        # reopen
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        return dest

    def add_task(self, name, category='General', w=0, color=None):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO tasks (name, category, w, color) VALUES (?,?,?,?)",
                        (name.strip(), category, int(bool(w)), color))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # existing, return id
            cur.execute("SELECT id FROM tasks WHERE name=?", (name.strip(),))
            r = cur.fetchone()
            return r['id'] if r else None

    def list_tasks(self, with_w_priority_first=True):
        order = "w DESC, name" if with_w_priority_first else "name"
        cur = self.conn.execute(f"SELECT * FROM tasks ORDER BY {order}")
        return [dict(row) for row in cur.fetchall()]

    def get_task(self, task_id):
        cur = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    def set_task_w(self, task_id, w):
        self.conn.execute("UPDATE tasks SET w=? WHERE id=?", (int(bool(w)), task_id))
        self.conn.commit()

    def remove_task(self, task_id):
        self.conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self.conn.commit()

    def start_entry(self, task_id, start_ts=None):
        start_ts = start_ts or datetime.now().isoformat()
        date_key = start_ts[:10]
        cur = self.conn.cursor()
        cur.execute("INSERT INTO entries (task_id, start_ts, date_key) VALUES (?,?,?)",
                    (task_id, start_ts, date_key))
        self.conn.commit()
        return cur.lastrowid

    def stop_entry(self, entry_id, end_ts=None):
        end_ts = end_ts or datetime.now().isoformat()
        cur = self.conn.cursor()
        # compute duration
        cur.execute("SELECT start_ts FROM entries WHERE id=?", (entry_id,))
        r = cur.fetchone()
        if not r:
            return False
        start = datetime.fromisoformat(r['start_ts'])
        end = datetime.fromisoformat(end_ts)
        dur = (end - start).total_seconds() / 3600.0
        cur.execute("UPDATE entries SET end_ts=?, duration_h=? WHERE id=?",
                    (end_ts, round(dur, 4), entry_id))
        self.conn.commit()
        return True

    def list_entries_for_date(self, date_key):
        cur = self.conn.execute(
            "SELECT e.*, t.name as task_name, t.w as task_w FROM entries e JOIN tasks t ON e.task_id=t.id WHERE e.date_key=? ORDER BY t.w DESC, t.name, e.start_ts",
            (date_key,))
        return [dict(r) for r in cur.fetchall()]

    def get_active_entries(self):
        cur = self.conn.execute("SELECT e.*, t.name as task_name FROM entries e JOIN tasks t ON e.task_id=t.id WHERE e.end_ts IS NULL")
        return [dict(r) for r in cur.fetchall()]

    def end_all_active(self):
        active = self.get_active_entries()
        for e in active:
            self.stop_entry(e['id'])
        return len(active)

    def update_entry(self, entry_id, start_ts, end_ts):
        # ensure timestamps
        date_key = start_ts[:10]
        try:
            s = datetime.fromisoformat(start_ts)
            e = datetime.fromisoformat(end_ts) if end_ts else None
        except Exception:
            raise
        dur = 0
        if e:
            dur = round((e - s).total_seconds()/3600.0, 4)
        self.conn.execute("UPDATE entries SET start_ts=?, end_ts=?, duration_h=?, date_key=? WHERE id=?",
                          (start_ts, end_ts, dur, date_key, entry_id))
        self.conn.commit()

    def delete_entry(self, entry_id):
        self.conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self.conn.commit()

    def export_date_to_df(self, date_key, only_w=True):
        rows = self.list_entries_for_date(date_key)
        if only_w:
            rows = [r for r in rows if r['task_w']]
        if not rows:
            return None
        data = []
        for r in rows:
            data.append({
                'Task': r['task_name'],
                'Start': r['start_ts'],
                'End': r['end_ts'] or '',
                'Duration_h': r['duration_h']
            })
        df = pd.DataFrame(data)
        summary = df.groupby('Task', as_index=False)['Duration_h'].sum()
        summary = summary.rename(columns={'Duration_h': 'Hours'})
        total = pd.DataFrame([{'Task': 'Total', 'Hours': summary['Hours'].sum()}])
        out = pd.concat([summary, total], ignore_index=True)
        return out

# ---------- UI layer ----------
class TimeTrackerUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Time Tracker — Improved")
        self.storage = Storage()
        self.selected_date = date.today()
        self.active_entry_map = {}  # task_id -> entry_id

        self._load_active_map()
        self._setup_ui()
        self._refresh_task_list()
        self._refresh_entries()
        # backup on startup occasionally
        self._maybe_backup()

        # updater
        self._updater()

    def _maybe_backup(self):
        try:
            # find last backup
            os.makedirs(BACKUP_DIR, exist_ok=True)
            files = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')])
            if not files or (len(files) > 0 and (datetime.now() - datetime.fromtimestamp(os.path.getmtime(os.path.join(BACKUP_DIR, files[-1])))).days >= AUTOBACKUP_DAYS):
                dest = self.storage.backup()
                print("DB backup created:", dest)
        except Exception as e:
            print("Backup failed:", e)

    def _load_active_map(self):
        active = self.storage.get_active_entries()
        for e in active:
            self.active_entry_map[e['task_id']] = e['id']

    def _setup_ui(self):
        # Layout: top controls, left tasks, right entries
        if USE_BOOTSTRAP:
            self.style = tb.Style()
            container = ttk.Frame(self.root, padding=10)
        else:
            self.style = None
            container = ttk.Frame(self.root, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(container)
        top.pack(fill=tk.X)

        # Date picker
        ttk.Label(top, text="Дата:").pack(side=tk.LEFT)
        self.date_entry = DateEntry(top, date_pattern='yyyy-mm-dd')
        self.date_entry.set_date(self.selected_date)
        self.date_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Перейти", command=self._on_date_change).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Сегодня", command=self._go_today).pack(side=tk.LEFT, padx=5)

        # Search / quick add
        ttk.Button(top, text="Добавить задачу", command=self._add_task_dialog).pack(side=tk.RIGHT, padx=5)
        ttk.Button(top, text="Резервная копия", command=self._manual_backup).pack(side=tk.RIGHT, padx=5)

        """
        Time Tracker App (improved v2)
        Features:
        - SQLite storage
        - Modernized UI with ttkbootstrap (if installed)
        - Date selector
        - START/STOP/Pause buttons
        - Task Combobox for adding
        - Daily report button (with table + totals)
        - Entry editing improved (table with inline editing of start/end times)
        - Export to Excel
        - Backup support
        """

        import os
        import sqlite3
        from datetime import datetime, date, timedelta
        import shutil
        import tkinter as tk
        from tkinter import ttk, messagebox, simpledialog, filedialog

        try:
            import ttkbootstrap as tb
            USE_BOOTSTRAP = True
        except Exception:
            USE_BOOTSTRAP = False

        try:
            from tkcalendar import DateEntry
        except Exception:
            raise SystemExit("Please install tkcalendar: pip install tkcalendar")

        try:
            import pandas as pd
        except Exception:
            pd = None

        DB_FILE = "time_tracker.db"
        BACKUP_DIR = "backups"

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
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        """

        class Storage:
            def __init__(self, path=DB_FILE):
                init_needed = not os.path.exists(path)
                self.conn = sqlite3.connect(path, check_same_thread=False)
                self.conn.row_factory = sqlite3.Row
                if init_needed:
                    self.conn.executescript(SCHEMA)
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

            def start_entry(self, task_id, start_ts=None):
                start_ts = start_ts or datetime.now().isoformat()
                date_key = start_ts[:10]
                cur = self.conn.cursor()
                cur.execute("INSERT INTO entries (task_id, start_ts, date_key) VALUES (?,?,?)",
                            (task_id, start_ts, date_key))
                self.conn.commit()
                return cur.lastrowid

            def stop_entry(self, entry_id, end_ts=None):
                end_ts = end_ts or datetime.now().isoformat()
                cur = self.conn.cursor()
                cur.execute("SELECT start_ts FROM entries WHERE id=?", (entry_id,))
                r = cur.fetchone()
                if not r:
                    return False
                start = datetime.fromisoformat(r['start_ts'])
                end = datetime.fromisoformat(end_ts)
                dur = (end - start).total_seconds() / 3600.0
                cur.execute("UPDATE entries SET end_ts=?, duration_h=? WHERE id=?",
                            (end_ts, round(dur, 2), entry_id))
                self.conn.commit()
                return True

            def list_entries_for_date(self, date_key):
                cur = self.conn.execute(
                    "SELECT e.*, t.name as task_name FROM entries e JOIN tasks t ON e.task_id=t.id WHERE e.date_key=? ORDER BY start_ts",
                    (date_key,))
                return [dict(r) for r in cur.fetchall()]

            def update_entry(self, entry_id, start_ts, end_ts):
                start = datetime.fromisoformat(start_ts)
                end = datetime.fromisoformat(end_ts)
                dur = round((end - start).total_seconds() / 3600.0, 2)
                self.conn.execute("UPDATE entries SET start_ts=?, end_ts=?, duration_h=?, date_key=? WHERE id=?",
                                  (start_ts, end_ts, dur, start.date().isoformat(), entry_id))
                self.conn.commit()

            def delete_entry(self, entry_id):
                self.conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
                self.conn.commit()

            def export_date_to_df(self, date_key):
                rows = self.list_entries_for_date(date_key)
                if not rows:
                    return None
                data = []
                for r in rows:
                    data.append({
                        'Task': r['task_name'],
                        'Start': r['start_ts'],
                        'End': r['end_ts'] or '',
                        'Duration_h': r['duration_h']
                    })
                df = pd.DataFrame(data)
                summary = df.groupby('Task', as_index=False)['Duration_h'].sum()
                total = pd.DataFrame([{'Task': 'Total', 'Duration_h': summary['Duration_h'].sum()}])
                return pd.concat([summary, total], ignore_index=True)

        class TimeTrackerUI:
            def __init__(self, root):
                self.root = root
                self.storage = Storage()
                self.selected_date = date.today()
                self._setup_ui()
                self._refresh()

            def _setup_ui(self):
                top = ttk.Frame(self.root)
                top.pack(fill=tk.X, pady=5)

                ttk.Label(top, text="Дата:").pack(side=tk.LEFT)
                self.date_entry = DateEntry(top, date_pattern='yyyy-mm-dd')
                self.date_entry.set_date(self.selected_date)
                self.date_entry.pack(side=tk.LEFT, padx=5)
                ttk.Button(top, text="Обновить", command=self._refresh).pack(side=tk.LEFT, padx=5)
                ttk.Button(top, text="Отчёт", command=self._show_report).pack(side=tk.LEFT, padx=5)
                ttk.Button(top, text="Экспорт", command=self._export_excel).pack(side=tk.LEFT, padx=5)

                ttk.Button(top, text="Добавить задачу", command=self._add_task_dialog).pack(side=tk.RIGHT, padx=5)

                self.tree = ttk.Treeview(self.root, columns=("Task", "Start", "End", "Duration"), show='headings')
                for c in ("Task", "Start", "End", "Duration"):
                    self.tree.heading(c, text=c)
                self.tree.pack(fill=tk.BOTH, expand=True)
                self.tree.bind('<Double-1>', self._edit_entry_dialog)

            def _refresh(self):
                for i in self.tree.get_children():
                    self.tree.delete(i)
                date_key = self.date_entry.get_date().isoformat()
                rows = self.storage.list_entries_for_date(date_key)
                for r in rows:
                    self.tree.insert('', 'end', iid=r['id'],
                                     values=(r['task_name'], r['start_ts'], r['end_ts'], r['duration_h']))

            def _add_task_dialog(self):
                dlg = tk.Toplevel(self.root)
                dlg.title("Новая задача")
                dlg.transient(self.root)
                dlg.grab_set()

                tasks = self.storage.list_tasks()
                task_names = [t["name"] for t in tasks]
                ttk.Label(dlg, text="Введите или выберите задачу:").pack(pady=5)
                combo_var = tk.StringVar()
                combo = ttk.Combobox(dlg, textvariable=combo_var, values=task_names)
                combo.pack(padx=10, pady=5, fill=tk.X)
                combo.focus()

                def on_ok():
                    name = combo_var.get().strip()
                    if not name:
                        messagebox.showwarning("Ошибка", "Название задачи не может быть пустым", parent=dlg)
                        return
                    self.storage.add_task(name)
                    dlg.destroy()

                ttk.Button(dlg, text="OK", command=on_ok).pack(pady=10)
                dlg.wait_window()

            def _edit_entry_dialog(self, event):
                sel = self.tree.selection()
                if not sel:
                    return
                entry_id = int(sel[0])
                item = self.tree.item(entry_id)
                vals = item['values']
                dlg = tk.Toplevel(self.root)
                dlg.title("Редактирование записи")
                dlg.transient(self.root)
                dlg.grab_set()

                ttk.Label(dlg, text="Start (YYYY-MM-DD HH:MM:SS)").pack()
                start_var = tk.StringVar(value=vals[1])
                ttk.Entry(dlg, textvariable=start_var).pack(fill=tk.X, padx=10, pady=5)

                ttk.Label(dlg, text="End (YYYY-MM-DD HH:MM:SS)").pack()
                end_var = tk.StringVar(value=vals[2])
                ttk.Entry(dlg, textvariable=end_var).pack(fill=tk.X, padx=10, pady=5)

                def save():
                    try:
                        self.storage.update_entry(entry_id, start_var.get(), end_var.get())
                        dlg.destroy()
                        self._refresh()
                    except Exception as e:
                        messagebox.showerror("Ошибка", str(e), parent=dlg)

                ttk.Button(dlg, text="Сохранить", command=save).pack(pady=10)
                ttk.Button(dlg, text="Отмена", command=dlg.destroy).pack()
                dlg.wait_window()

            def _show_report(self):
                date_key = self.date_entry.get_date().isoformat()
                rows = self.storage.list_entries_for_date(date_key)
                totals = {}
                for r in rows:
                    totals[r['task_name']] = totals.get(r['task_name'], 0) + r['duration_h']
                dlg = tk.Toplevel(self.root)
                dlg.title(f"Отчёт {date_key}")
                tree = ttk.Treeview(dlg, columns=("Task", "Hours"), show='headings')
                tree.heading("Task", text="Task")
                tree.heading("Hours", text="Hours")
                tree.pack(fill=tk.BOTH, expand=True)
                for t, h in totals.items():
                    tree.insert('', 'end', values=(t, round(h, 2)))
                tree.insert('', 'end', values=("Total", round(sum(totals.values()), 2)))

            def _export_excel(self):
                if pd is None:
                    messagebox.showerror("Ошибка", "pandas не установлен")
                    return
                date_key = self.date_entry.get_date().isoformat()
                df = self.storage.export_date_to_df(date_key)
                if df is None:
                    messagebox.showinfo("Экспорт", "Нет данных")
                    return
                path = filedialog.asksaveasfilename(defaultextension='.xlsx',
                                                    filetypes=[('Excel', '*.xlsx')],
                                                    initialfile=f'report_{date_key}.xlsx')
                if not path:
                    return
                df.to_excel(path, index=False)
                messagebox.showinfo("Экспорт", f"Сохранено: {path}")

        def main():
            root = tb.Window(themename='flatly') if USE_BOOTSTRAP else tk.Tk()
            ui = TimeTrackerUI(root)
            root.mainloop()

        if __name__ == '__main__':
            main()
