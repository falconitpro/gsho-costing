import { Component, type ReactNode } from "react";
import { reportError } from "../errorReporter";

/** D-117: ошибка отрисовки экрана не оставляет белую страницу — показывает, что делать, и отправляет ошибку администратору. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { reportError(error, "экран"); }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="card" style={{ maxWidth: 640, margin: "60px auto" }}>
        <h2 style={{ marginTop: 0 }}>Экран не открылся из-за ошибки</h2>
        <p>Ошибка уже отправлена администратору. Обновите страницу — обычно этого достаточно. Если повторяется — откройте другой раздел и сообщите в поддержку, что делали перед ошибкой.</p>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn" onClick={() => window.location.reload()}>Обновить страницу</button>
          <button className="btn ghost" onClick={() => { window.location.href = "/"; }}>На рабочее место</button>
        </div>
      </div>);
  }
}
