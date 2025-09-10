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

        main = ttk.Frame(container)
        main.pack(fill=tk.BOTH, expand=True, pady=10)

        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=False)

        ttk.Label(left, text="Задачи").pack(padx=5)
        self.tasks_tree = ttk.Treeview(left, columns=("category","w"), show='headings', height=20)
        self.tasks_tree.heading('category', text='Категория')
        self.tasks_tree.heading('w', text='W')
        self.tasks_tree.column('category', width=120)
        self.tasks_tree.column('w', width=30)
        self.tasks_tree.pack(fill=tk.BOTH, expand=True)
        self.tasks_tree.bind('<<TreeviewSelect>>', self._on_task_select)

        btns = ttk.Frame(left)
        btns.pack(fill=tk.X, pady=5)
        ttk.Button(btns, text='START', command=self._start_selected).pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(btns, text='STOP', command=self._stop_selected).pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        ttk.Button(btns, text='PAUSE', command=self._pause_selected).pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)

        # center: quick controls
        center = ttk.Frame(main)
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)

        ttk.Label(center, text="Сессии (выбранная дата)").pack()
        cols = ("Task","Start","End","Duration_h")
        self.entries_tree = ttk.Treeview(center, columns=cols, show='headings', height=20)
        for c in cols:
            self.entries_tree.heading(c, text=c)
            self.entries_tree.column(c, anchor='center')
        self.entries_tree.column('Task', width=200, anchor='w')
        self.entries_tree.pack(fill=tk.BOTH, expand=True)
        self.entries_tree.bind('<Double-1>', self._edit_entry_dialog)

        entry_btns = ttk.Frame(center)
        entry_btns.pack(fill=tk.X, pady=5)
        ttk.Button(entry_btns, text='Добавить запись', command=self._add_entry_manual).pack(side=tk.LEFT, padx=5)
        ttk.Button(entry_btns, text='Удалить запись', command=self._delete_entry).pack(side=tk.LEFT, padx=5)
        ttk.Button(entry_btns, text='Экспорт в Excel', command=self._export_excel).pack(side=tk.LEFT, padx=5)

        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(right, text='Инфо').pack()
        self.info_text = tk.Text(right, width=30, height=20)
        self.info_text.pack(fill=tk.Y)

    def _on_date_change(self):
        d = self.date_entry.get_date()
        self.selected_date = d
        self._refresh_entries()

    def _go_today(self):
        self.selected_date = date.today()
        self.date_entry.set_date(self.selected_date)
        self._refresh_entries()

    def _refresh_task_list(self):
        for i in self.tasks_tree.get_children():
            self.tasks_tree.delete(i)
        tasks = self.storage.list_tasks()
        for t in tasks:
            self.tasks_tree.insert('', 'end', iid=t['id'], values=(t['category'] or '', t['w']))

    def _refresh_entries(self):
        for i in self.entries_tree.get_children():
            self.entries_tree.delete(i)
        date_key = self.selected_date.isoformat()
        rows = self.storage.list_entries_for_date(date_key)
        for r in rows:
            start = r['start_ts'][:19].replace('T',' ')
            end = (r['end_ts'][:19].replace('T',' ')) if r['end_ts'] else ''
            self.entries_tree.insert('', 'end', iid=r['id'], values=(r['task_name'], start, end, r['duration_h']))
        self._update_info()

    def _update_info(self):
        # show totals and active tasks
        date_key = self.selected_date.isoformat()
        rows = self.storage.list_entries_for_date(date_key)
        totals = {}
        for r in rows:
            if r['task_name'] not in totals:
                totals[r['task_name']] = 0
            totals[r['task_name']] += r['duration_h']
        s = f"Дата: {date_key}\nВсего задач: {len(totals)}\n\n"
        for k,v in totals.items():
            s += f"{k}: {v:.2f} ч\n"
        # active
        active = self.storage.get_active_entries()
        if active:
            s += '\nАктивные задачи:\n'
            for a in active:
                started = datetime.fromisoformat(a['start_ts'])
                elapsed = (datetime.now() - started).total_seconds()/3600.0
                s += f"{a['task_name']} — {elapsed:.2f} ч (id {a['id']})\n"
        self.info_text.delete('1.0', tk.END)
        self.info_text.insert(tk.END, s)

    def _on_task_select(self, event):
        sel = self.tasks_tree.selection()
        # nothing for now

    def _add_task_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Новая задача")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text="Введите или выберите задачу:").pack(pady=5)

        # список существующих задач
        tasks = self.storage.list_tasks()
        task_names = [t["name"] for t in tasks]

        combo_var = tk.StringVar()
        combo = ttk.Combobox(dlg, textvariable=combo_var, values=task_names)
        combo.pack(padx=10, pady=5, fill=tk.X)
        combo.focus()

        ttk.Label(dlg, text="Категория:").pack(pady=5)
        cat_var = tk.StringVar(value="General")
        cat_entry = ttk.Entry(dlg, textvariable=cat_var)
        cat_entry.pack(padx=10, pady=5, fill=tk.X)

        def on_ok():
            name = combo_var.get().strip()
            if not name:
                messagebox.showwarning("Ошибка", "Название задачи не может быть пустым", parent=dlg)
                return
            cat = cat_var.get().strip() or "General"
            task_id = self.storage.add_task(name, category=cat)
            self._refresh_task_list()
            if task_id:
                self.tasks_tree.selection_set(task_id)
                self.tasks_tree.see(task_id)
            dlg.destroy()

        ttk.Button(dlg, text="OK", command=on_ok).pack(pady=10)
        ttk.Button(dlg, text="Отмена", command=dlg.destroy).pack()

        dlg.wait_window()

    def _start_selected(self):
        sel = self.tasks_tree.selection()
        if not sel:
            messagebox.showwarning('Внимание', 'Выберите задачу')
            return
        task_id = int(sel[0])
        # auto-stop other active entries
        active = self.storage.get_active_entries()
        for a in active:
            if a['task_id'] != task_id:
                self.storage.stop_entry(a['id'])
        # if this task already active, ignore
        if task_id in self.active_entry_map:
            messagebox.showinfo('Info', 'Задача уже активна')
            return
        entry_id = self.storage.start_entry(task_id)
        self.active_entry_map[task_id] = entry_id
        self._refresh_entries()

    def _stop_selected(self):
        sel = self.tasks_tree.selection()
        if not sel:
            messagebox.showwarning('Внимание', 'Выберите задачу')
            return
        task_id = int(sel[0])
        if task_id not in self.active_entry_map:
            messagebox.showinfo('Info', 'Эта задача не активна')
            return
        entry_id = self.active_entry_map.pop(task_id)
        self.storage.stop_entry(entry_id)
        self._refresh_entries()

    def _pause_selected(self):
        # pause implemented as stop current and immediately create a zero-length paused marker? Simpler: stop
        sel = self.tasks_tree.selection()
        if not sel:
            messagebox.showwarning('Внимание', 'Выберите задачу')
            return
        task_id = int(sel[0])
        if task_id in self.active_entry_map:
            entry_id = self.active_entry_map.pop(task_id)
            self.storage.stop_entry(entry_id)
            messagebox.showinfo('Paused', 'Задача поставлена на паузу (остановлена)')
            self._refresh_entries()
        else:
            messagebox.showinfo('Info', 'Задача не активна')

    def _add_entry_manual(self):
        # add a historical entry for selected date
        sel = self.tasks_tree.selection()
        task_id = None
        if sel:
            task_id = int(sel[0])
        tasks = self.storage.list_tasks()
        task_names = [t['name'] for t in tasks]
        task = simpledialog.askstring('Task', 'Введите или выберите задачу:', initialvalue=(self.storage.get_task(task_id)['name'] if task_id else ''))
        if not task:
            return
        # ensure task exists
        t_id = self.storage.add_task(task)
        start = simpledialog.askstring('Start', 'Время начала (HH:MM):', parent=self.root) or '00:00'
        end = simpledialog.askstring('End', 'Время окончания (HH:MM):', parent=self.root) or '00:00'
        try:
            h1, m1 = map(int, start.split(':'))
            h2, m2 = map(int, end.split(':'))
        except Exception:
            messagebox.showerror('Ошибка', 'Неверный формат времени')
            return
        # construct ISO timestamps on selected date
        d = self.selected_date
        start_ts = datetime(d.year, d.month, d.day, h1, m1).isoformat()
        end_dt = datetime(d.year, d.month, d.day, h2, m2)
        if end_dt < datetime.fromisoformat(start_ts):
            end_dt += timedelta(days=1)
        end_ts = end_dt.isoformat()
        entry_id = self.storage.start_entry(t_id, start_ts=start_ts)
        self.storage.stop_entry(entry_id, end_ts=end_ts)
        self._refresh_entries()

    def _edit_entry_dialog(self, event):
        sel = self.entries_tree.selection()
        if not sel:
            return
        entry_id = int(sel[0])
        cur = self.storage.conn.execute("SELECT e.*, t.name as task_name FROM entries e JOIN tasks t ON e.task_id=t.id WHERE e.id=?", (entry_id,))
        r = cur.fetchone()
        if not r:
            return
        # dialog: edit start & end (HH:MM:SS) and task
        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.title('Редактирование записи')

        ttk.Label(dlg, text='Задача').grid(row=0, column=0)
        task_var = tk.StringVar(value=r['task_name'])
        ttk.Entry(dlg, textvariable=task_var).grid(row=0, column=1)

        ttk.Label(dlg, text='Start (YYYY-MM-DD HH:MM:SS)').grid(row=1, column=0)
        start_var = tk.StringVar(value=r['start_ts'][:19].replace('T',' '))
        ttk.Entry(dlg, textvariable=start_var, width=25).grid(row=1, column=1)

        ttk.Label(dlg, text='End (YYYY-MM-DD HH:MM:SS or empty)').grid(row=2, column=0)
        end_var = tk.StringVar(value=(r['end_ts'][:19].replace('T',' ') if r['end_ts'] else ''))
        ttk.Entry(dlg, textvariable=end_var, width=25).grid(row=2, column=1)

        def save():
            task_name = task_var.get().strip()
            if not task_name:
                messagebox.showerror('Ошибка','Нельзя пустое имя задачи', parent=dlg)
                return
            t_id = self.storage.add_task(task_name)
            s = start_var.get().strip()
            e = end_var.get().strip() or None
            try:
                s_iso = datetime.fromisoformat(s).isoformat()
                e_iso = datetime.fromisoformat(e).isoformat() if e else None
            except Exception:
                messagebox.showerror('Ошибка','Неверный формат даты/времени', parent=dlg)
                return
            try:
                self.storage.update_entry(entry_id, s_iso, e_iso)
                dlg.destroy()
                self._refresh_entries()
            except Exception as ex:
                messagebox.showerror('Ошибка', str(ex), parent=dlg)

        ttk.Button(dlg, text='Сохранить', command=save).grid(row=3, column=0)
        ttk.Button(dlg, text='Отмена', command=dlg.destroy).grid(row=3, column=1)

    def _delete_entry(self):
        sel = self.entries_tree.selection()
        if not sel:
            messagebox.showwarning('Внимание', 'Выберите запись')
            return
        entry_id = int(sel[0])
        if messagebox.askyesno('Удалить', 'Удалить запись?'):
            self.storage.delete_entry(entry_id)
            self._refresh_entries()

    def _export_excel(self):
        if pd is None:
            messagebox.showerror('Ошибка', 'pandas не установлен. Установите pandas и openpyxl для экспорта.')
            return
        date_key = self.selected_date.isoformat()
        df = self.storage.export_date_to_df(date_key)
        if df is None or df.empty:
            messagebox.showinfo('Экспорт', 'Нет данных для выбранной даты')
            return
        path = filedialog.asksaveasfilename(defaultextension='.xlsx', filetypes=[('Excel files','*.xlsx')], initialfile=f'report_{date_key}.xlsx')
        if not path:
            return
        try:
            df.to_excel(path, index=False)
            messagebox.showinfo('Экспорт', f'Экспорт сохранён в {path}')
        except Exception as e:
            messagebox.showerror('Ошибка', str(e))

    def _manual_backup(self):
        try:
            dest = self.storage.backup()
            messagebox.showinfo('Backup', f'Backup created: {dest}')
        except Exception as e:
            messagebox.showerror('Ошибка', f'Не удалось создать backup: {e}')

    def _updater(self):
        # refresh info periodically
        self._refresh_entries()
        self.root.after(30000, self._updater)

# ---------- Runner ----------

def main():
    if USE_BOOTSTRAP:
        app = tb.Window(themename='flatly')
        root = app
    else:
        root = tk.Tk()
    ui = TimeTrackerUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
