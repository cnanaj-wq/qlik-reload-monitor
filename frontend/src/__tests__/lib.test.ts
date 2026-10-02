import { describe, expect, it } from "vitest";
import {
  fmtBytes,
  fmtClock,
  fmtDateTime,
  fmtDuration,
  fmtInt,
  fmtTime,
  msBetween,
} from "../lib/format";
import { worstStatus } from "../lib/status";
import {
  buildTimeline,
  errorParts,
  groupBySection,
  mergeEvents,
  OTHER_SECTION,
  stepLabel,
  summarizeQvds,
} from "../lib/timeline";
import { ev, resetSeq, runningEvents } from "../test/fixtures";

describe("formats", () => {
  it("durées", () => {
    expect(fmtDuration(832)).toBe("832 ms");
    expect(fmtDuration(3200)).toBe("3.2 s");
    expect(fmtDuration(47800)).toBe("47.8 s");
    expect(fmtDuration(63000)).toBe("1m 03s");
    expect(fmtDuration(761000)).toBe("12m 41s");
    expect(fmtDuration(3_720_000)).toBe("1h 02m");
    expect(fmtClock(64000)).toBe("00:01:04");
  });

  it("volumes et lignes", () => {
    expect(fmtInt(1842556)).toBe("1 842 556");
    expect(fmtBytes(838 * 1024)).toBe("838 KB");
    expect(fmtBytes(4.6 * 1024 * 1024)).toBe("4.6 MB");
    expect(fmtBytes(31.8 * 1024 * 1024)).toBe("31.8 MB");
    expect(fmtBytes(1.4 * 1024 ** 3)).toBe("1.4 GB");
  });

  it("dates affichées sans conversion de fuseau", () => {
    expect(fmtDateTime("2026-10-01T22:47:15.975551")).toBe("01/10/2026 22:47:15");
    expect(fmtTime("2026-10-01T22:47:15")).toBe("22:47:15");
    expect(msBetween("2026-10-01T22:47:15.500", "2026-10-01T22:48:16.000")).toBe(60500);
  });

  it("statut le plus grave", () => {
    expect(worstStatus(["SUCCESS", "WARNING", "RUNNING"])).toBe("WARNING");
    expect(worstStatus(["WARNING", "ERROR"])).toBe("ERROR");
    expect(worstStatus([])).toBe("PENDING");
  });
});

describe("fusion des événements", () => {
  it("déduplique par seq et trie par seq", () => {
    const [a, b, c] = runningEvents();
    expect(mergeEvents([a!, c!], [b!, c!, a!]).map((e) => e.seq)).toEqual([1, 2, 3]);
  });
  it("ajoute en fin de liste quand le flux est ordonné", () => {
    const evs = runningEvents();
    const merged = mergeEvents(evs.slice(0, 5), evs.slice(5));
    expect(merged.map((e) => e.seq)).toEqual(evs.map((e) => e.seq));
  });
  it("renvoie la même liste si rien n'arrive", () => {
    const evs = runningEvents();
    expect(mergeEvents(evs, [])).toBe(evs);
  });
});

describe("timeline", () => {
  it("construit les lignes attendues", () => {
    const items = buildTimeline(runningEvents());
    expect(items.map((i) => i.title)).toEqual([
      "Reload démarré",
      "DIMENSIONS",
      "CLIENTS",
      "CLIENTS",
      "CLIENTS.qvd",
      "3.4 MB",
      "4.6 MB",
      "CLIENTS.qvd stabilisé",
      "VENTES.qvd",
      "12.0 MB",
    ]);
    expect(items[3]!.detail).toContain("42 318 lignes");
    expect(items[6]!.detail).toBe("+1.1 MB");
    expect(items[5]!.minor).toBe(true);
  });

  it("libellé de l'étape courante", () => {
    expect(stepLabel("TABLE_PROGRESS VENTES")).toBe("Chargement VENTES");
    expect(stepLabel("QVD_STABLE VENTES.qvd")).toBe("VENTES.qvd stabilisé");
    expect(stepLabel(null)).toBeNull();
  });
});

describe("sections", () => {
  it("rattache les mesures QVD sans section à la section d'écriture", () => {
    const g = groupBySection(runningEvents(), true);
    expect(g.head.map((e) => e.event_type)).toEqual(["RELOAD_START"]);
    expect(g.sections.map((s) => s.name)).toEqual(["DIMENSIONS"]);
    expect(g.sections[0]!.events).toHaveLength(9);
    expect(g.sections[0]!.status).toBe("RUNNING");
  });

  it("ne masque jamais un événement sans section", () => {
    resetSeq(0);
    const evs = [
      ev({ event_type: "RELOAD_START" }),
      ev({ event_type: "WARNING", status: "WARNING", message: "orphelin" }),
      ev({ event_type: "RELOAD_END", status: "WARNING" }),
    ];
    const g = groupBySection(evs, false);
    const other = g.sections.find((s) => s.name === OTHER_SECTION);
    expect(other?.events.map((e) => e.message)).toEqual(["orphelin"]);
    expect(other?.status).toBe("WARNING");
    expect(g.tail).toHaveLength(1);
  });

  it("statut et durée de section", () => {
    resetSeq(0);
    const evs = [
      ev({ event_type: "SECTION_START", section: "S", timestamp: "2026-10-01T10:00:00" }),
      ev({ event_type: "SECTION_END", section: "S", status: "SUCCESS", timestamp: "2026-10-01T10:00:03.200" }),
      ev({ event_type: "SECTION_START", section: "F" }),
      ev({ event_type: "ERROR", section: "F", status: "ERROR", message: "x" }),
    ];
    const [s, f] = groupBySection(evs, false).sections;
    expect(s!.status).toBe("SUCCESS");
    expect(s!.durationMs).toBe(3200);
    expect(f!.status).toBe("ERROR");
  });
});

describe("QVD", () => {
  it("écriture puis stabilisation, sans pourcentage", () => {
    const [clients, ventes] = summarizeQvds(runningEvents());
    expect(clients).toMatchObject({ name: "CLIENTS.qvd", phase: "stable", sizeBytes: 4_823_449 });
    expect(clients!.lastDelta).toBe(4_823_449 - 3_617_587);
    expect(ventes).toMatchObject({ name: "VENTES.qvd", phase: "writing", sizeBytes: 12 * 1024 * 1024 });
    expect(Object.keys(ventes!)).not.toContain("percent");
  });

  it("un nouveau QVD_WRITE_START repart de zéro", () => {
    resetSeq(0);
    const evs = [
      ev({ event_type: "QVD_WRITE_START", qvd: "A.qvd" }),
      ev({ event_type: "QVD_SIZE_CHANGE", qvd: "A.qvd", qvd_size_bytes: 100 }),
      ev({ event_type: "QVD_STABLE", qvd: "A.qvd", qvd_size_bytes: 100 }),
      ev({ event_type: "QVD_WRITE_START", qvd: "A.qvd" }),
      ev({ event_type: "QVD_SIZE_CHANGE", qvd: "A.qvd", qvd_size_bytes: 40 }),
    ];
    const [a] = summarizeQvds(evs);
    expect(a).toMatchObject({ phase: "writing", sizeBytes: 40, lastDelta: 40 });
    expect(a!.series).toHaveLength(1);
  });

  it("écrit mais pas encore stable", () => {
    resetSeq(0);
    const [a] = summarizeQvds([
      ev({ event_type: "QVD_SIZE_CHANGE", qvd: "A.qvd", qvd_size_bytes: 5 }),
      ev({ event_type: "QVD_WRITE_END", qvd: "A.qvd" }),
    ]);
    expect(a!.phase).toBe("written");
  });
});

describe("erreurs", () => {
  it("extrait le code et la description", () => {
    expect(
      errorParts("SQL##f - SqlState: 42S02, ErrorCode: 942, ErrorMsg: ORA-00942: table or view does not exist"),
    ).toEqual({ code: "ORA-00942", text: "table or view does not exist" });
    expect(errorParts("Champ introuvable")).toEqual({ code: null, text: "Champ introuvable" });
    expect(errorParts(null).text).toBe("Message non disponible");
  });
});
