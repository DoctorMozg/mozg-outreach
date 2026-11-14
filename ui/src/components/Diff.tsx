import type { ReactElement } from "react";
// Word-level diff by longest common subsequence; letters are a few hundred words at most.
type Piece = { kind: "same" | "added" | "removed"; text: string };

export function diffWords(before: string, after: string): Piece[] {
  const a = before.split(/(\s+)/);
  const b = after.split(/(\s+)/);
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i]![j] = a[i] === b[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
    }
  }
  const out: Piece[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      out.push({ kind: "same", text: a[i++]! });
      j++;
    } else if (j < b.length && (i === a.length || lcs[i]![j + 1]! >= lcs[i + 1]![j]!)) {
      out.push({ kind: "added", text: b[j++]! });
    } else {
      out.push({ kind: "removed", text: a[i++]! });
    }
  }
  return out;
}

export function Diff({ before, after }: { before: string; after: string }): ReactElement {
  return (
    <pre className="diff">
      {diffWords(before, after).map((piece, n) =>
        piece.kind === "same" ? piece.text : <span key={n} className={piece.kind}>{piece.text}</span>,
      )}
    </pre>
  );
}
