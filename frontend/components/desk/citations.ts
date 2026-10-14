/** Where a stored answer's citations go. A citation's start_index and end_index address the stored message text,
 *  which is the text the thread shows. A number is drawn inline only where those offsets are whole numbers inside the
 *  text and, when the citation stored the cited words, address exactly those words. Nothing is placed by searching
 *  for the words: a citation that cannot be placed exactly is kept in the source list only. */

type Stored = Record<string, unknown>;
export type Source = { url: URL; title: string; quote: string; number: number };
export type Piece = string | number[];
export type Placed = { paragraphs: Piece[][]; sources: Source[] };

const text = (value: unknown) => (typeof value === "string" ? value : "");

/** A link is drawn only for a web address; anything else a source supplied stays plain text. */
export function webAddress(value: unknown): URL | null {
  try {
    const url = new URL(text(value));
    return url.protocol === "https:" || url.protocol === "http:" ? url : null;
  } catch {
    return null;
  }
}

// A cited span that is only a link written into the answer, such as "([site](https://...))", is not a quote.
export const linkOnly = (value: string) => /^\(?\[[^\]]*\]\([^)]*\)\)?$/.test(value.trim());

function span(message: string, citation: Stored): [number, number] | null {
  const start = citation.start_index, end = citation.end_index;
  if (!Number.isInteger(start) || !Number.isInteger(end)) return null;
  const [from, to] = [start as number, end as number];
  if (from < 0 || to <= from || to > message.length) return null;
  const quoted = text(citation.cited_text);
  return !quoted || message.slice(from, to) === quoted ? [from, to] : null;
}

export function placeCitations(message: string, citations: Stored[]): Placed {
  // Sources are numbered by page, in the order they are first cited; one entry per page and quoted words.
  const pages: string[] = [];
  const sources: Source[] = [];
  const spans = new Map<string, { from: number; to: number; numbers: number[] }>();
  for (const citation of citations) {
    const url = webAddress(citation.url);
    if (!url) continue;
    if (!pages.includes(url.href)) pages.push(url.href);
    const number = pages.indexOf(url.href) + 1;
    const quote = text(citation.cited_text);
    if (!sources.some(item => item.url.href === url.href && item.quote === quote))
      sources.push({ url, quote, number, title: text(citation.title) || url.hostname });
    const at = span(message, citation);
    if (!at) continue;
    const key = at.join("-");
    const entry = spans.get(key) ?? { from: at[0], to: at[1], numbers: [] };
    if (!entry.numbers.includes(number)) entry.numbers.push(number);
    spans.set(key, entry);
  }

  // Paragraphs and where each starts and ends in the message, split as the thread splits them.
  const ranges: [number, number][] = [];
  let start = 0;
  for (const gap of message.matchAll(/\n{2,}/g)) {
    ranges.push([start, gap.index!]);
    start = gap.index! + gap[0].length;
  }
  ranges.push([start, message.length]);

  // A link-only span is replaced by its number when it overlaps no other cited span; any other span keeps its words
  // and gets its number where it ends, in the paragraph holding its last character.
  const marks = [...spans.values()].map(entry => {
    const alone = ![...spans.values()].some(other => other !== entry && other.from < entry.to && entry.from < other.to);
    const paragraph = ranges.findIndex(([from, to]) => entry.to - 1 >= from && entry.to - 1 < to);
    const inside = paragraph >= 0 && entry.from >= ranges[paragraph][0];
    const replace = alone && inside && linkOnly(message.slice(entry.from, entry.to));
    return { ...entry, numbers: [...entry.numbers].sort((a, b) => a - b), paragraph, at: replace ? entry.from : Math.min(entry.to, ranges[Math.max(paragraph, 0)][1]), cut: replace ? entry.to : null };
  }).filter(mark => mark.paragraph >= 0).sort((a, b) => a.at - b.at || a.from - b.from);

  const paragraphs = ranges.map(([from, to], index): Piece[] => {
    const pieces: Piece[] = [];
    let cursor = from;
    for (const mark of marks.filter(item => item.paragraph === index)) {
      if (mark.at < cursor) continue;
      const before = message.slice(cursor, mark.at);
      pieces.push(mark.cut === null ? before : before.trimEnd(), mark.numbers);
      cursor = mark.cut ?? mark.at;
    }
    pieces.push(message.slice(cursor, to));
    return pieces.filter(piece => piece !== "");
  });
  return { paragraphs, sources };
}
