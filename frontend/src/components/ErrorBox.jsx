export default function ErrorBox({ message, detail, onRetry }) {
  return <div className="err-box" role="alert">
    <p>{message}</p>
    {onRetry && <button type="button" className="btn ghost" onClick={onRetry}>Retry</button>}
    {detail && <details><summary>Details</summary><pre>{detail}</pre></details>}
  </div>;
}
