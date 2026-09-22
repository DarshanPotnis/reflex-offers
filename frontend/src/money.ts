/**
 * Display formatting for the decimal strings the server sends.
 *
 * This file never calls Number() and never does arithmetic. The server is
 * the only thing that computes money — it stores integer ten-thousandths of
 * a dollar precisely so no float ever touches a price. Turning "4708.00"
 * into a Number here to add a comma would quietly undo that, so the grouping
 * below is done on the string itself.
 */

export function formatUsd(amount: string | null | undefined): string {
  if (amount == null || amount === "") return "—";
  const negative = amount.startsWith("-");
  const unsigned = negative ? amount.slice(1) : amount;
  const [whole, fraction = ""] = unsigned.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const cents = fraction.length >= 2 ? fraction : fraction.padEnd(2, "0");
  return `${negative ? "-" : ""}$${grouped}.${cents}`;
}

/** Counts are counts, not money — plain integers are safe here. */
export function formatCount(value: number | null | undefined): string {
  if (value == null) return "—";
  return value.toLocaleString("en-US");
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return count === 1 ? one : many;
}

export function formatWhen(iso: string): string {
  const then = new Date(iso);
  const seconds = Math.round((Date.now() - then.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    return `${m} ${plural(m, "minute")} ago`;
  }
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    return `${h} ${plural(h, "hour")} ago`;
  }
  return then.toLocaleDateString();
}
