import { Fragment, type ReactNode } from "react";
import { webAddress } from "./citations";
import styles from "./Thread.module.css";

/** The Markdown answers use: headings, paragraphs, bold and italic, lists, tables, links and code.
 *  Everything becomes React elements, never HTML: model text cannot add markup or scripts, and a link is drawn
 *  only for a web address. ponytail: one list level, no block quotes or images; add them when answers need them. */

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*|__[^_\n]+__)|(\*[^*\s][^*\n]*\*|(?<![\w])_[^_\s][^_\n]*_(?![\w]))|(\[[^\]\n]+\]\([^)\s]+\))/g;

export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(INLINE)) {
    const [token, code, strong, em, link] = match;
    if (match.index > cursor) out.push(text.slice(cursor, match.index));
    const key = match.index;
    if (code) out.push(<code key={key}>{code.slice(1, -1)}</code>);
    else if (strong) out.push(<strong key={key}>{inline(strong.slice(2, -2))}</strong>);
    else if (em) out.push(<em key={key}>{inline(em.slice(1, -1))}</em>);
    else if (link) {
      const [, label, href] = link.match(/^\[([^\]]+)\]\(([^)]+)\)$/)!;
      const url = webAddress(href);
      out.push(url ? <a key={key} href={url.href} target="_blank" rel="noopener noreferrer">{inline(label)}</a> : <Fragment key={key}>{inline(label)}</Fragment>);
    } else out.push(token);
    cursor = match.index + token.length;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

const cells = (line: string) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cell => cell.trim());
const DIVIDER = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const BULLET = /^\s*[-*+]\s+/;
const NUMBER = /^\s*\d+[.)]\s+/;

export function blocks(text: string): ReactNode[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = out.length;
    if (!line.trim()) {
      i++;
    } else if (line.trim().startsWith("```")) {
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith("```"); i++) code.push(lines[i]);
      i++;
      out.push(<pre key={key} className={styles.code}><code>{code.join("\n")}</code></pre>);
    } else if (/^#{1,6}\s/.test(line)) {
      const level = line.match(/^#+/)![0].length;
      const content = inline(line.replace(/^#+\s+/, "").replace(/\s+#+\s*$/, ""));
      out.push(level <= 2 ? <h3 key={key} className={styles.mdHeading}>{content}</h3> : <h4 key={key} className={styles.mdHeading}>{content}</h4>);
      i++;
    } else if (line.includes("|") && i + 1 < lines.length && DIVIDER.test(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows.push(cells(lines[i]));
      out.push(
        <div key={key} className={styles.tableWrap}>
          <table className={styles.mdTable}>
            <thead><tr>{head.map((cell, at) => <th key={at} scope="col">{inline(cell)}</th>)}</tr></thead>
            <tbody>{rows.map((row, at) => <tr key={at}>{head.map((_, col) => <td key={col}>{inline(row[col] ?? "")}</td>)}</tr>)}</tbody>
          </table>
        </div>
      );
    } else if (BULLET.test(line) || NUMBER.test(line)) {
      const marker = BULLET.test(line) ? BULLET : NUMBER;
      const items: string[] = [];
      for (; i < lines.length && (marker.test(lines[i]) || (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length)); i++)
        if (marker.test(lines[i])) items.push(lines[i].replace(marker, ""));
        else items[items.length - 1] += ` ${lines[i].trim()}`;
      const List = marker === BULLET ? "ul" : "ol";
      out.push(<List key={key} className={styles.mdList}>{items.map((item, at) => <li key={at}>{inline(item)}</li>)}</List>);
    } else {
      const paragraph: string[] = [];
      for (; i < lines.length && lines[i].trim() && !/^#{1,6}\s|^\s*```/.test(lines[i]) && !BULLET.test(lines[i]) && !NUMBER.test(lines[i])
        && !(lines[i].includes("|") && i + 1 < lines.length && DIVIDER.test(lines[i + 1])); i++) paragraph.push(lines[i].trim());
      out.push(<p key={key}>{inline(paragraph.join(" "))}</p>);
    }
  }
  return out;
}
