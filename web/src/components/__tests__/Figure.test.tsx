import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EM_DASH, Figure, formatFigure } from "../Figure";

describe("Figure", () => {
  it("renders a figure the way a person says it, not the way it is stored", () => {
    // 4730362129360 is not read, it is decoded.
    expect(formatFigure(4_730_362_129_360, "money")).toBe("$4.73T");
    expect(formatFigure(98_767_000_000, "money")).toBe("$98.8B");
    expect(formatFigure(319.97, "money")).toBe("$319.97");
    expect(formatFigure(0.0209, "percent")).toBe("2.09%");
    expect(formatFigure(38.29, "multiple")).toBe("38.3×");
  });

  it("never renders a non-zero score as zero", () => {
    // The rule this file exists for. Two-decimal formatting once turned 77%
    // of 1,956 real scores into "0.00" on screen, and a reader would have
    // concluded the scorer produced nothing.
    const tiny = [1.62e-6, 0.0014, 0.0029, 0.00049];
    for (const value of tiny) {
      const text = formatFigure(value, "score");
      expect(text).not.toBe("0.00");
      expect(text).not.toBe("0");
      expect(Number(text)).toBeGreaterThan(0);
    }
  });

  it("renders an exact zero as a bare zero, distinguishably", () => {
    // So it cannot be confused with a small number that was rounded away.
    expect(formatFigure(0, "score")).toBe("0");
  });

  it("renders a missing value as an em dash, never as zero or a blank", () => {
    for (const absent of [null, undefined, NaN]) {
      expect(formatFigure(absent, "money")).toBe(EM_DASH);
    }
  });

  it("tells a screen reader that a missing value is not recorded", () => {
    render(<Figure value={null} kind="money" />);
    expect(screen.getByLabelText("not recorded")).toBeInTheDocument();
  });
});
