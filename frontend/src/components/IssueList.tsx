import type { Issue } from "../types";

const LABEL: Record<Issue["kind"], string> = {
  fixed: "Fixed",
  warning: "Check",
  auto_excluded: "Left out",
  needs_decision: "Needs you",
};

export function IssueList({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return null;
  return (
    <ul className="issues">
      {issues.map((issue, index) => (
        <li key={`${issue.code}-${index}`} className={`issue-${issue.kind}`}>
          <span className="issue-code">{LABEL[issue.kind]}</span>
          {issue.message}
        </li>
      ))}
    </ul>
  );
}
