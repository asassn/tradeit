import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MissingRecord, NotYetProduced, Provenance, Unresolved } from "../Absence";

describe("absence is content, not an error state", () => {
  it("states WHY a value is absent rather than leaving a blank", () => {
    // A blank reads as zero or not-applicable. The truth is usually more
    // useful: this company loses money.
    render(
      <Unresolved
        what="Price to earnings"
        reason="trailing twelve-month NetIncomeLoss is -228,125"
      />,
    );
    expect(screen.getByText(/not computed/)).toBeInTheDocument();
    expect(screen.getByText(/-228,125/)).toBeInTheDocument();
  });

  it("raises an alert when the record is silent, and only then", () => {
    // A session with empty lists and no `missing` was quiet; one with empty
    // lists AND `missing` was never written down. Those are different
    // operational facts and only the second is an alert.
    const { unmount } = render(<MissingRecord missing={["executions", "journal"]} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/cannot be walked/);
    expect(screen.getByRole("alert")).toHaveTextContent(/nobody wrote the record/);
    unmount();

    render(<MissingRecord missing={[]} />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("note")).toHaveTextContent(/Walkable end to end/);
  });

  it("names what an unpopulated capability needs, not a date", () => {
    render(
      <NotYetProduced
        capability="Breakouts"
        built="Thirteen states and four scores per event."
        needs="Patterns first — no structural boundary exists outside full-01."
      />,
    );
    expect(screen.getByText(/nothing recorded yet/)).toBeInTheDocument();
    expect(screen.getByText(/Patterns first/)).toBeInTheDocument();
    // Not a promise.
    expect(screen.queryByText(/coming soon/i)).toBeNull();
  });

  it("keeps the arithmetic present but folded, never truncated", () => {
    const lines = ["a = b / c", "d = e - f", "g = h + i"];
    render(<Provenance lines={lines} />);
    // Every line is in the DOM — a disclosure hides it from the eye, not
    // from the reader who opens it.
    for (const line of lines) {
      expect(screen.getByText(line)).toBeInTheDocument();
    }
    expect(screen.getByText("Show the working")).toBeInTheDocument();
  });

  it("renders nothing rather than an empty disclosure", () => {
    const { container } = render(<Provenance lines={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
