import { formatDuration } from "../utils/format";

test("formats final and live durations with a controllable clock", () => {
  expect(
    formatDuration("2026-08-09T10:00:00Z", "2026-08-09T10:01:05Z"),
  ).toBe("1m 5s");
  expect(
    formatDuration("2026-08-09T10:00:00Z", null, () => Date.parse("2026-08-09T11:02:03Z")),
  ).toBe("1h 2m 3s");
});
