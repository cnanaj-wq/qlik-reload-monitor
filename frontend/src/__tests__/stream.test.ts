import { beforeEach, describe, expect, it, vi } from "vitest";
import { openReloadStream } from "../api/stream";
import type { ReloadEvent, StreamStatus } from "../types";
import { ev, fakeFactory, FakeEventSource, resetSeq } from "../test/fixtures";

function setup(afterSeq = 0) {
  const received: ReloadEvent[] = [];
  const statuses: StreamStatus[] = [];
  const stream = openReloadStream({
    afterSeq,
    createSource: fakeFactory,
    backoffMs: [100, 200],
    onEvent: (e) => received.push(e),
    onStatus: (s) => statuses.push(s),
  });
  return { stream, received, statuses };
}

describe("flux SSE", () => {
  beforeEach(() => {
    FakeEventSource.reset();
    resetSeq(10);
  });

  it("s'ouvre après le dernier seq connu et passe LIVE", () => {
    const { statuses } = setup(10);
    expect(FakeEventSource.last().url).toBe("/api/stream?after_seq=10");
    FakeEventSource.last().open();
    expect(statuses).toEqual(["connecting", "live"]);
  });

  it("transmet les événements dans l'ordre et ignore les doublons", () => {
    const { received, stream } = setup(10);
    const src = FakeEventSource.last();
    const a = ev({ event_type: "TABLE_PROGRESS", table: "T", rows: 1 });
    const b = ev({ event_type: "TABLE_PROGRESS", table: "T", rows: 2 });
    src.emit(a);
    src.emit(b);
    src.emit(a); // doublon
    src.emit({ ...b, seq: 5 }); // déjà dépassé
    src.emit("pas du json");
    expect(received.map((e) => e.seq)).toEqual([11, 12]);
    expect(stream.lastSeq).toBe(12);
  });

  it("se reconnecte depuis le dernier seq reçu, avec délai croissant", () => {
    vi.useFakeTimers();
    const { statuses } = setup(10);
    const first = FakeEventSource.last();
    first.open();
    first.emit(ev({ event_type: "RELOAD_START" }));
    first.fail();
    expect(first.closed).toBe(true); // reconnexion native coupée
    expect(statuses.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(99);
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.last().url).toBe("/api/stream?after_seq=11");
    FakeEventSource.last().fail();
    vi.advanceTimersByTime(199);
    expect(FakeEventSource.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(3);
    FakeEventSource.last().open();
    expect(statuses.at(-1)).toBe("live");
    vi.useRealTimers();
  });

  it("close() arrête toute reconnexion", () => {
    vi.useFakeTimers();
    const { stream } = setup(0);
    FakeEventSource.last().fail();
    stream.close();
    vi.advanceTimersByTime(10_000);
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.useRealTimers();
  });
});
