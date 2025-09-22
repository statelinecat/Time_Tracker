"""
Time Tracker App (версия с Treeview и кнопками)
"""

import os
import sqlite3
from datetime import datetime, date
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkcalendar import DateEntry  # Добавляем импорт календаря

DB_FILE = "../time_tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
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

    def add_task(self, name, w=0):
        cur = self.conn.cursor()
        try:
            cur.execute("INSERT INTO tasks (name, w) VALUES (?,?)",
                        (name.strip(), int(bool(w))))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            cur.execute("SELECT id FROM tasks WHERE name=?", (name.strip(),))
            r = cur.fetchone()
            return r['id'] if r else None

    def get_tasks(self):
        """Получить список всех задач"""
        cur = self.conn.execute("SELECT * FROM tasks ORDER BY w DESC, name")
        return [dict(r) for r in cur.fetchall()]

    def update_task_w(self, task_id, w):
        self.conn.execute("UPDATE tasks SET w=? WHERE id=?", (int(bool(w)), task_id))
        self.conn.commit()

    def get_task_id_by_name(self, name):
        """Получить ID задачи по имени"""
        cur = self.conn.execute("SELECT id FROM tasks WHERE name=?", (name.strip(),))
        r = cur.fetchone()
        return r['id'] if r else None

    def list_tasks(self):
        cur = self.conn.execute("SELECT * FROM tasks ORDER BY w DESC, name")
        return [dict(r) for r in cur.fetchall()]

    def start_entry(self, task_id, date_key=None):
        """Запустить задачу (не останавливая другие)"""
        start_ts = datetime.now().isoformat()
        date_key = date_key or start_ts[:10]
        cur = self.conn.cursor()
        cur.execute("INSERT INTO entries (task_id, start_ts, date_key, active) VALUES (?,?,?,1)",
                    (task_id, start_ts, date_key))
        self.conn.commit()
        return cur.lastrowid

    def stop_entry(self, entry_id=None):
        """Остановить конкретную запись"""
        end_ts = datetime.now().isoformat()
        if entry_id:
            cur = self.conn.execute("SELECT start_ts FROM entries WHERE id=?", (entry_id,))
            r = cur.fetchone()
            if not r:
                return False
            start_ts = r['start_ts']
        else:
            return False  # Теперь нужно явно указывать entry_id

        start = datetime.fromisoformat(start_ts)
        end = datetime.fromisoformat(end_ts)
        dur = (end - start).total_seconds() / 3600.0
        self.conn.execute("UPDATE entries SET end_ts=?, duration_h=?, active=0 WHERE id=?",
                          (end_ts, round(dur, 2), entry_id))
        self.conn.commit()
        return True

    def pause_all(self):
        """Остановить все активные задачи"""
        end_ts = datetime.now().isoformat()
        cur = self.conn.execute("SELECT id, start_ts FROM entries WHERE active=1")
        active_entries = cur.fetchall()

        for entry in active_entries:
            start_ts = entry['start_ts']
            start = datetime.fromisoformat(start_ts)
            end = datetime.fromisoformat(end_ts)
            dur = (end - start).total_seconds() / 3600.0
            self.conn.execute("UPDATE entries SET end_ts=?, duration_h=?, active=0 WHERE id=?",
                              (end_ts, round(dur, 2), entry['id']))

        self.conn.commit()
        return len(active_entries) > 0

    def get_active_entries(self):
        """Получить все активные записи"""
        cur = self.conn.execute("""
            SELECT e.*, t.name as task_name 
            FROM entries e 
            JOIN tasks t ON e.task_id = t.id 
            WHERE e.active=1
        """)
        return [dict(r) for r in cur.fetchall()]

    def list_entries_for_date(self, date_key):
        cur = self.conn.execute(
            """SELECT e.*, t.name as task_name, t.w as w, t.id as task_id
               FROM entries e
               JOIN tasks t ON e.task_id=t.id
               WHERE e.date_key=?
               ORDER BY t.w DESC, e.start_ts""",
            (date_key,))
        return [dict(r) for r in cur.fetchall()]

    def get_tasks_with_entries_for_date(self, date_key):
        """Получить все задачи с записями на указанную дату, включая задачи без записей"""
        # Сначала получаем все задачи
        tasks = self.list_tasks()

        # Получаем записи на указанную дату
        entries = self.list_entries_for_date(date_key)

        # Создаем словарь для быстрого доступа к записям по task_id
        entries_by_task = {}
        for entry in entries:
            task_id = entry['task_id']
            if task_id not in entries_by_task:
                entries_by_task[task_id] = []
            entries_by_task[task_id].append(entry)

        # Формируем результат: все задачи с их записями
        result = []
        for task in tasks:
            task_entries = entries_by_task.get(task['id'], [])
            result.append({
                'task': task,
                'entries': task_entries
            })

        return result

    def get_daily_report(self, date_key):
        """Получить отчет по задачам за указанную дату"""
        cur = self.conn.execute("""
            SELECT 
                t.name as task_name,
                t.w as important,
                SUM(e.duration_h) as total_hours,
                COUNT(e.id) as entries_count
            FROM entries e
            JOIN tasks t ON e.task_id = t.id
            WHERE e.date_key = ?
            GROUP BY t.id, t.name, t.w
            ORDER BY t.w DESC, total_hours DESC
        """, (date_key,))

        report_data = [dict(r) for r in cur.fetchall()]

        # Получаем общую сумму часов
        total_hours = sum(item['total_hours'] for item in report_data)

        return {
            'date': date_key,
            'tasks': report_data,
            'total_hours': total_hours,
            'tasks_count': len(report_data)
        }

    def update_entry_time(self, entry_id, start_time, end_time, entry_date):
        """Обновить время начала и окончания записи"""
        try:
            # Преобразуем время в полные timestamp
            start_ts = f"{entry_date}T{start_time}:00"
            end_ts = f"{entry_date}T{end_time}:00" if end_time else None

            # Проверяем корректность времени
            start_dt = datetime.fromisoformat(start_ts)
            if end_time:
                end_dt = datetime.fromisoformat(end_ts)
                if end_dt <= start_dt:
                    return False, "Время окончания должно быть позже времени начала"
                duration = (end_dt - start_dt).total_seconds() / 3600.0
            else:
                end_ts = None
                duration = 0.0

            self.conn.execute(
                "UPDATE entries SET start_ts=?, end_ts=?, duration_h=?, date_key=? WHERE id=?",
                (start_ts, end_ts, round(duration, 2), entry_date, entry_id)
            )
            self.conn.commit()
            return True, "Время успешно обновлено"
        except ValueError:
            return False, "Неверный формат времени"

    def get_entry(self, entry_id):
        """Получить запись по ID"""
        cur = self.conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,))
        r = cur.fetchone()
        return dict(r) if r else None

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
            "UPDATE entries SET start_ts=?, end_ts=?, duration_h=?, date_key=? WHERE id=?",
            (start_ts, end_ts, dur, start.date().isoformat(), entry_id))
        self.conn.commit()

    def delete_entry(self, entry_id):
        self.conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self.conn.commit()


class TimeTrackerUI:
    def __init__(self, root):
        self.root = root
        self.storage = Storage()
        self.selected_date = date.today()
        self.active_entries = []  # Список активных записей
        self.w_vars = {}  # Словарь для хранения переменных чекбоксов
        self._setup_ui()
        self._refresh()
        self._update_timer()

    def _setup_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Top frame with controls
        top = ttk.Frame(main_frame)
        top.pack(fill=tk.X, pady=5)

        ttk.Label(top, text="Дата:").pack(side=tk.LEFT)

        # Календарь для выбора даты
        self.date_var = tk.StringVar(value=date.today().strftime("%d.%m.%y"))
        self.date_entry = DateEntry(
            top,
            textvariable=self.date_var,
            date_pattern='dd.mm.yy',
            width=12,
            background='darkblue',
            foreground='white',
            borderwidth=2
        )
        self.date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(top, text="Обновить", command=self._refresh).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Отчет", command=self._show_report).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Добавить задачу", command=self._add_task_dialog).pack(side=tk.RIGHT, padx=5)

        # Treeview frame
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Treeview with scrollbar
        cols = ("Task", "W", "Start", "End", "Duration", "Actions")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=15)

        # Configure columns
        self.tree.heading("Task", text="Задача")
        self.tree.heading("W", text="W")
        self.tree.heading("Start", text="Начало")
        self.tree.heading("End", text="Конец")
        self.tree.heading("Duration", text="Длительность")
        self.tree.heading("Actions", text="Действия")

        self.tree.column("Task", width=200)
        self.tree.column("W", width=30, anchor="center")
        self.tree.column("Start", width=80, anchor="center")
        self.tree.column("End", width=80, anchor="center")
        self.tree.column("Duration", width=80, anchor="center")
        self.tree.column("Actions", width=150, anchor="center")

        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Button frame at the bottom
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=5)

        self.start_btn = ttk.Button(button_frame, text="START", command=self._start_selected_task)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(button_frame, text="STOP", command=self._stop_selected_task, state="disabled")
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.pause_btn = ttk.Button(button_frame, text="PAUSE ALL", command=self._pause_all_tasks)
        self.pause_btn.pack(side=tk.LEFT, padx=5)

        # Status label
        self.status_label = ttk.Label(button_frame, text="Нет активных задач")
        self.status_label.pack(side=tk.LEFT, padx=10)

    def _show_report(self):
        """Показать отчет за выбранную дату"""
        date_key = self._get_date_key()
        report = self.storage.get_daily_report(date_key)

        # Создаем окно отчета
        report_window = tk.Toplevel(self.root)
        report_window.title(f"Отчет за {date_key}")
        report_window.geometry("600x400")

        # Main frame
        main_frame = ttk.Frame(report_window, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Заголовок
        ttk.Label(main_frame, text=f"Отчет за {date_key}",
                  font=("Arial", 14, "bold")).pack(pady=10)

        # Treeview для отчета
        report_cols = ("Task", "W", "Entries", "Hours")
        report_tree = ttk.Treeview(main_frame, columns=report_cols, show="headings", height=10)

        report_tree.heading("Task", text="Задача")
        report_tree.heading("W", text="W")
        report_tree.heading("Entries", text="Записей")
        report_tree.heading("Hours", text="Часов")

        report_tree.column("Task", width=250)
        report_tree.column("W", width=30, anchor="center")
        report_tree.column("Entries", width=80, anchor="center")
        report_tree.column("Hours", width=80, anchor="center")

        # Добавляем данные в отчет
        for task in report['tasks']:
            report_tree.insert("", tk.END, values=(
                task['task_name'],
                "✓" if task['important'] else "",
                task['entries_count'],
                f"{task['total_hours']:.2f}"
            ))

        report_tree.pack(fill=tk.BOTH, expand=True, pady=10)

        # Итоговая информация
        summary_frame = ttk.Frame(main_frame)
        summary_frame.pack(fill=tk.X, pady=10)

        ttk.Label(summary_frame, text=f"Всего задач: {report['tasks_count']}",
                  font=("Arial", 10)).pack(side=tk.LEFT)

        ttk.Label(summary_frame, text=f"Общее время: {report['total_hours']:.2f} ч",
                  font=("Arial", 10, "bold")).pack(side=tk.RIGHT)

        # Кнопка закрытия
        ttk.Button(main_frame, text="Закрыть", command=report_window.destroy).pack(pady=10)

    def _update_timer(self):
        """Обновление таймера для активных задач"""
        if self.active_entries:
            status_text = "Активные задачи: "
            active_tasks = []
            for entry in self.active_entries:
                start_time = datetime.fromisoformat(entry['start_ts'])
                current_time = datetime.now()
                duration = current_time - start_time
                hours = duration.total_seconds() / 3600
                active_tasks.append(f"{entry['task_name']} ({hours:.2f} ч)")

            status_text += ", ".join(active_tasks)
            self.status_label.config(text=status_text)
        else:
            self.status_label.config(text="Нет активных задач")

        # Обновляем каждую секунду
        self.root.after(1000, self._update_timer)

    def _get_date_key(self):
        try:
            # Получаем дату из календаря
            selected_date = self.date_entry.get_date()
            return selected_date.strftime("%Y-%m-%d")
        except Exception:
            return date.today().isoformat()

    def _refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

        # Проверяем активные задачи
        self.active_entries = self.storage.get_active_entries()

        # Обновляем состояние кнопок
        if self.active_entries:
            self.pause_btn.config(state="normal")
        else:
            self.pause_btn.config(state="disabled")

        date_key = self._get_date_key()
        tasks_with_entries = self.storage.get_tasks_with_entries_for_date(date_key)

        for task_data in tasks_with_entries:
            task = task_data['task']
            entries = task_data['entries']

            if entries:
                # Если есть записи для этой задачи, показываем их
                for entry in entries:
                    start_t = datetime.fromisoformat(entry['start_ts']).strftime('%H:%M')
                    end_t = datetime.fromisoformat(entry['end_ts']).strftime('%H:%M') if entry['end_ts'] else ''
                    # Добавляем стрелку для активных задач
                    is_active = any(active_entry['id'] == entry['id'] for active_entry in self.active_entries)
                    task_name = ('▶ ' if is_active else '') + task['name']

                    item_id = self.tree.insert("", tk.END, values=(
                        task_name,
                        "✓" if task['w'] else "",
                        start_t,
                        end_t,
                        entry['duration_h'],
                        f"ID: {entry['id']}"
                    ))

                    # Сохраняем информацию о задаче
                    self.w_vars[item_id] = (task['id'], task['name'], task['w'])

            else:
                # Если нет записей, показываем только задачу
                item_id = self.tree.insert("", tk.END, values=(
                    task['name'],
                    "✓" if task['w'] else "",
                    "",
                    "",
                    "",
                    ""
                ))

                # Сохраняем информацию о задаче
                self.w_vars[item_id] = (task['id'], task['name'], task['w'])

        # Привязываем обработчики кликов
        self.tree.bind("<Double-1>", self._on_tree_double_click)

    def _on_tree_double_click(self, event):
        """Обработчик двойного клика по дереву"""
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)

        if not item:
            return

        # Получаем значения из строки
        values = self.tree.item(item)['values']
        if not values or len(values) < 6:
            return

        entry_id_str = values[5]
        try:
            entry_id = int(entry_id_str.split(': ')[1])
        except (IndexError, ValueError):
            return  # Не удалось получить ID записи

        if column == "#2":  # Колонка W - изменение важности
            task_id, task_name, current_w = self.w_vars.get(item, (None, None, 0))
            if task_id:
                new_w = not current_w
                self.storage.update_task_w(task_id, new_w)
                # Обновляем отображение
                self.tree.set(item, "W", "✓" if new_w else "")
                # Обновляем кэш
                self.w_vars[item] = (task_id, task_name, new_w)

        elif column == "#3" or column == "#4":  # Колонки Start или End - редактирование времени
            self._edit_entry_time(entry_id, column)

    def _edit_entry_time(self, entry_id, column):
        """Редактирование времени записи"""
        # Получаем данные записи
        entry = self.storage.get_entry(entry_id)
        if not entry:
            return

        # Создаем диалоговое окно
        dlg = tk.Toplevel(self.root)
        dlg.title("Редактирование времени")
        dlg.geometry("300x150")
        dlg.resizable(False, False)

        main_frame = ttk.Frame(dlg, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Поле для времени начала
        ttk.Label(main_frame, text="Время начала (ЧЧ:ММ):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        start_var = tk.StringVar(value=datetime.fromisoformat(entry['start_ts']).strftime('%H:%M'))
        start_entry = ttk.Entry(main_frame, textvariable=start_var, width=10)
        start_entry.grid(row=0, column=1, padx=5, pady=5)

        # Поле для времени окончания
        ttk.Label(main_frame, text="Время окончания (ЧЧ:ММ):").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        end_value = datetime.fromisoformat(entry['end_ts']).strftime('%H:%M') if entry['end_ts'] else ''
        end_var = tk.StringVar(value=end_value)
        end_entry = ttk.Entry(main_frame, textvariable=end_var, width=10)
        end_entry.grid(row=1, column=1, padx=5, pady=5)

        # Дата записи
        entry_date = entry['date_key']

        def save():
            start_time = start_var.get().strip()
            end_time = end_var.get().strip()

            # Проверяем формат времени
            if not self._validate_time_format(start_time):
                messagebox.showerror("Ошибка", "Неверный формат времени начала (должно быть ЧЧ:ММ)")
                return

            if end_time and not self._validate_time_format(end_time):
                messagebox.showerror("Ошибка", "Неверный формат времени окончания (должно быть ЧЧ:ММ)")
                return

            # Обновляем запись
            success, message = self.storage.update_entry_time(entry_id, start_time, end_time, entry_date)
            if success:
                messagebox.showinfo("Успех", message)
                dlg.destroy()
                self._refresh()
            else:
                messagebox.showerror("Ошибка", message)

        # Кнопки
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)

        ttk.Button(btn_frame, text="Сохранить", command=save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Отмена", command=dlg.destroy).pack(side=tk.LEFT, padx=5)

        dlg.transient(self.root)
        dlg.grab_set()
        dlg.wait_window()

    def _validate_time_format(self, time_str):
        """Проверка формата времени ЧЧ:ММ"""
        try:
            if not time_str:
                return False
            parts = time_str.split(':')
            if len(parts) != 2:
                return False
            hours = int(parts[0])
            minutes = int(parts[1])
            return 0 <= hours <= 23 and 0 <= minutes <= 59
        except ValueError:
            return False

    def _update_task_w(self, task_id, w_value):
        """Обновить флаг W для задачи"""
        self.storage.update_task_w(task_id, w_value)

    def _start_selected_task(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Предупреждение", "Выберите задачу для старта")
            return

        item = self.tree.item(selection[0])
        values = item['values']

        # Проверяем, есть ли уже активная запись для этой задачи
        task_name = values[0].replace('▶ ', '').strip()

        # Находим task_id по имени задачи
        task_id = self.storage.get_task_id_by_name(task_name)

        if task_id:
            self.storage.start_entry(task_id, self._get_date_key())
            self.active_entries = self.storage.get_active_entries()
            self._refresh()

    def _stop_selected_task(self):
        """Остановить выбранную активную задачу"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Предупреждение", "Выберите задачу для остановки")
            return

        item = self.tree.item(selection[0])
        values = item['values']

        # Получаем ID записи из последнего столбца
        entry_id_str = values[5]
        try:
            entry_id = int(entry_id_str.split(': ')[1])
            # Проверяем, активна ли эта запись
            if any(entry['id'] == entry_id for entry in self.active_entries):
                self.storage.stop_entry(entry_id)
                self.active_entries = self.storage.get_active_entries()
                self._refresh()
            else:
                messagebox.showwarning("Предупреждение", "Выбранная задача не активна")
        except (IndexError, ValueError):
            messagebox.showerror("Ошибка", "Не удалось определить ID записи")

    def _pause_all_tasks(self):
        """Остановить все активные задачи"""
        if self.active_entries:
            self.storage.pause_all()
            self.active_entries = []
            self._refresh()

    def _add_task_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Новая задача")
        dlg.geometry("400x150")
        dlg.resizable(False, False)

        # Main frame
        main_frame = ttk.Frame(dlg, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Выпадающий список с существующими задачи
        ttk.Label(main_frame, text="Выберите или введите новую задачу:").grid(
            row=0, column=0, sticky="w", padx=5, pady=5)

        # Получаем существующие задачи
        tasks = self.storage.get_tasks()
        task_names = [task['name'] for task in tasks]

        task_var = tk.StringVar()
        task_combo = ttk.Combobox(main_frame, textvariable=task_var, values=task_names, state='normal')
        task_combo.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        # Чекбокс для W
        w_var = tk.BooleanVar()
        ttk.Checkbutton(main_frame, text="Важная задача (W)", variable=w_var).grid(
            row=1, column=0, columnspan=2, padx=5, pady=10, sticky="w")

        def save():
            name = task_var.get().strip()
            if not name:
                messagebox.showerror("Ошибка", "Название задачи не может быть пустым")
                return

            self.storage.add_task(name, w=w_var.get())
            dlg.destroy()
            self._refresh()  # Обновляем отображение после добавления задачи

        # Кнопки
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=20)

        ttk.Button(btn_frame, text="Сохранить", command=save).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="Отмена", command=dlg.destroy).pack(side=tk.LEFT, padx=10)

        # Настройка весов для растягивания
        main_frame.columnconfigure(1, weight=1)

        dlg.transient(self.root)
        dlg.grab_set()
        dlg.wait_window()


def main():
    root = tk.Tk()
    root.title("Time Tracker")
    root.geometry("800x600")

    # Добавьте эти строки для установки иконки
    try:
        # Для Windows - используйте .ico файл
        root.iconbitmap("app.ico")
    except:
        try:
            # Для других ОС или если .ico не работает
            icon = tk.PhotoImage(file="app.png")
            root.iconphoto(True, icon)
        except:
            print("Не удалось загрузить иконку. Убедитесь, что файл app.ico или app.png находится в той же папке")

    app = TimeTrackerUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()