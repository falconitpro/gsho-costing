import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { STATUS_TITLES, type Task } from "../types";

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  useEffect(() => { api.get<Task[]>("/api/tasks/my").then(setTasks); }, []);
  return (
    <>
      <h1>Мои задачи</h1>
      <div className="card">
        <table>
          <thead><tr><th>Сделка</th><th>Задача</th><th>Блок</th><th>Срок</th><th>Статус сделки</th></tr></thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.id}>
                <td><Link to={`/deals/${t.deal_id}`}>{t.deal_number}</Link></td><td>{t.title}</td><td>{t.block}</td>
                <td>{fmtDate(t.due)} {t.overdue && <span className="badge bad">просрочено</span>}</td>
                <td>{STATUS_TITLES[t.deal_status] || t.deal_status}</td>
              </tr>
            ))}
            {tasks.length === 0 && <tr><td colSpan={5} className="muted">Задач нет</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
