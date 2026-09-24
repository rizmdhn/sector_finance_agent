// Minimal inline-markdown renderer for chat bubbles — the model's replies only
// ever use bold/italic/inline-code/links and paragraph breaks, not full
// markdown (tables, headers, images), so a regex pass beats pulling in
// react-markdown for this.
import type { ReactNode } from "react";

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /(\*\*.+?\*\*|__.+?__|`.+?`|\*[^*]+?\*|_[^_]+?_|\[.+?\]\(.+?\))/g;
  const parts = text.split(pattern);
  return parts.filter(Boolean).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={key}>{part.slice(1, -1)}</code>;
    }
    if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    const link = part.match(/^\[(.+)\]\((.+)\)$/);
    if (link) {
      return (
        <a key={key} href={link[2]} target="_blank" rel="noreferrer">
          {link[1]}
        </a>
      );
    }
    return part;
  });
}

export function Markdown({ text }: { text: string }): ReactNode {
  return text.split(/\n{2,}/).map((block, blockIndex) => {
    const lines = block.split("\n");
    return (
      <p key={blockIndex}>
        {lines.map((line, lineIndex) => (
          <span key={lineIndex}>
            {renderInline(line, `${blockIndex}-${lineIndex}`)}
            {lineIndex < lines.length - 1 && <br />}
          </span>
        ))}
      </p>
    );
  });
}
