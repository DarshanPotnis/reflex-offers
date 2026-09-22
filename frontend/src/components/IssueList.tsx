import { KIND } from "../kinds";
import type { Issue } from "../types";

export function IssueList({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return null;
  return (
    <ul className="issues">
      {issues.map((issue, index) => (
        <li
          key={`${issue.code}-${index}`}
          className={`issue-${issue.kind} ${KIND[issue.kind].className}`}
        >
          <span className="issue-code">{KIND[issue.kind].label}</span>
          {issue.message}
        </li>
      ))}
    </ul>
  );
}
