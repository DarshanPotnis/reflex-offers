import { plural } from "../money";

/** What the reader skipped or ignored, so nothing disappears silently. */
export function NoticesPanel({ notices }: { notices: string[] }) {
  if (notices.length === 0) return null;
  return (
    <details className="card notices">
      <summary>
        {notices.length} {plural(notices.length, "notice")} about this sheet
      </summary>
      <ul>
        {notices.map((notice) => (
          <li key={notice}>{notice}</li>
        ))}
      </ul>
    </details>
  );
}
