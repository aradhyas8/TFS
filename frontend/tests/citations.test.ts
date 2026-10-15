import { test, expect } from "@playwright/test";
import { placeCitations } from "../components/desk/citations";

// A stored citation's start_index and end_index address the stored message text, the same text the thread shows.
// A number is drawn inline only where those offsets are valid; anything else stays in the source list only.
const cite = (url: string, start: number | null, end: number | null, cited_text: string, title = "") =>
  ({ type: "url_citation", url, title, cited_text, ...(start === null ? {} : { start_index: start, end_index: end }) });
const A = "https://a.example/one";
const B = "https://b.example/two";

/** Paragraphs as text, with each mark written as [n] or [n,m]. */
const render = (placed: ReturnType<typeof placeCitations>) =>
  placed.paragraphs.map(pieces => pieces.map(piece => (typeof piece === "string" ? piece : `[${piece.join(",")}]`)).join(""));

test("a citation is placed at its stored offsets, not at the first matching phrase", () => {
  const text = "Revenue grew. Margins held. Revenue grew again.";
  const second = text.lastIndexOf("Revenue grew");
  const placed = placeCitations(text, [cite(A, second, second + "Revenue grew".length, "Revenue grew")]);
  expect(render(placed)).toEqual(["Revenue grew. Margins held. Revenue grew[1] again."]);
});

test("offsets that do not address the cited words place nothing inline, and the source is still listed", () => {
  const text = "Revenue grew. Margins held.";
  const placed = placeCitations(text, [cite(A, 0, 7, "Margins"), cite(B, 40, 52, "out of range")]);
  expect(render(placed)).toEqual([text]);
  expect(placed.sources.map(source => [source.number, source.url.href])).toEqual([[1, A], [2, B]]);
});

test("a citation without offsets is never guessed from its text", () => {
  const text = "Acme raised guidance this week.";
  const placed = placeCitations(text, [cite(A, null, null, "Acme raised guidance")]);
  expect(render(placed)).toEqual([text]);
  expect(placed.sources).toHaveLength(1);
});

test("several sources on one span share one place, numbered by page", () => {
  const text = "Orders softened in the quarter.";
  const end = "Orders softened".length;
  const placed = placeCitations(text, [cite(A, 0, end, "Orders softened"), cite(B, 0, end, "Orders softened"), cite(A, 0, end, "Orders softened")]);
  expect(render(placed)).toEqual(["Orders softened[1,2] in the quarter."]);
  expect(placed.sources.map(source => source.number)).toEqual([1, 2]);
});

test("a span that is only a link written into the answer is replaced by its number", () => {
  const link = "([a.example](https://a.example/one))";
  const text = `Guidance rose ${link}. Costs fell.`;
  const at = text.indexOf(link);
  const placed = placeCitations(text, [cite(A, at, at + link.length, link)]);
  expect(render(placed)).toEqual(["Guidance rose[1]. Costs fell."]);
});

test("offsets in a later paragraph mark that paragraph only", () => {
  const text = "First paragraph says growth.\n\nSecond paragraph says growth.";
  const at = text.lastIndexOf("growth");
  const placed = placeCitations(text, [cite(A, at, at + 6, "growth")]);
  expect(render(placed)).toEqual(["First paragraph says growth.", "Second paragraph says growth[1]."]);
});

test("a span across a paragraph break is marked once, where it ends", () => {
  const text = "One.\n\nTwo.";
  const placed = placeCitations(text, [cite(A, 0, text.length, text)]);
  expect(render(placed)).toEqual(["One.", "Two.[1]"]);
});

test("the same page cited at two places keeps one number and is marked at both", () => {
  const text = "Sales rose. Profit rose.";
  const placed = placeCitations(text, [cite(A, 0, 10, "Sales rose"), cite(A, 12, 23, "Profit rose")]);
  expect(render(placed)).toEqual(["Sales rose[1]. Profit rose[1]."]);
  expect(placed.sources).toHaveLength(2);
  expect(new Set(placed.sources.map(source => source.number))).toEqual(new Set([1]));
});

test("only web addresses are kept as sources", () => {
  const placed = placeCitations("Text.", [cite("javascript:alert(1)", 0, 4, "Text"), cite("not a url", null, null, "")]);
  expect(placed.sources).toEqual([]);
  expect(render(placed)).toEqual(["Text."]);
});
