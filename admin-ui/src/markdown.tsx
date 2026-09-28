// Minimal markdown renderer for chat bubbles — the model's replies regularly
// use bold/italic/inline-code/links, headings, and GFM-style pipe tables (real
// financial reports come back as tables), so those are the ones worth
// supporting; still not full markdown (no nested lists, images, blockquotes) —
// a regex/line-based pass beats pulling in react-markdown for this scope.
import { Fragment, type ReactNode } from "react";

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

function Paragraph({ lines, keyPrefix }: { lines: string[]; keyPrefix: string }) {
  return (
    <p>
      {lines.map((line, lineIndex) => (
        <span key={lineIndex}>
          {renderInline(line, `${keyPrefix}-${lineIndex}`)}
          {lineIndex < lines.length - 1 && <br />}
        </span>
      ))}
    </p>
  );
}

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;

function isTableBlock(lines: string[]): boolean {
  return lines.length >= 2 && lines[0].includes("|") && /^[\s:|-]+$/.test(lines[1]) && lines[1].includes("-");
}

function parseTableRow(line: string): string[] {
  let trimmed = line.trim();
  if (trimmed.startsWith("|")) trimmed = trimmed.slice(1);
  if (trimmed.endsWith("|")) trimmed = trimmed.slice(0, -1);
  return trimmed.split("|").map((cell) => cell.trim());
}

function Table({ lines, blockIndex }: { lines: string[]; blockIndex: number }) {
  const header = parseTableRow(lines[0]);
  const rows = lines.slice(2).map(parseTableRow);
  return (
    <div className="markdown-table-wrap">
      <table>
        <thead>
          <tr>
            {header.map((cell, i) => (
              <th key={i}>{renderInline(cell, `${blockIndex}-h-${i}`)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => (
                <td key={c}>{renderInline(cell, `${blockIndex}-${r}-${c}`)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Markdown({ text }: { text: string }): ReactNode {
  return text.split(/\n{2,}/).map((block, blockIndex) => {
    const lines = block.split("\n");

    // A heading only ever starts a block here (the model doesn't blank-line
    // separate a heading from the text right under it), so check line 0, not
    // "is this whole block just a heading" — the rest of the block still
    // renders as a normal paragraph right after it.
    const headingMatch = lines[0].match(HEADING_RE);
    if (headingMatch) {
      const Tag = HEADING_TAGS[Math.min(headingMatch[1].length, 6) - 1];
      const rest = lines.slice(1);
      return (
        <Fragment key={blockIndex}>
          <Tag>{renderInline(headingMatch[2], `${blockIndex}-h`)}</Tag>
          {rest.length > 0 && <Paragraph lines={rest} keyPrefix={`${blockIndex}-r`} />}
        </Fragment>
      );
    }

    if (isTableBlock(lines)) {
      return <Table key={blockIndex} lines={lines} blockIndex={blockIndex} />;
    }

    return <Paragraph key={blockIndex} lines={lines} keyPrefix={`${blockIndex}`} />;
  });
}
